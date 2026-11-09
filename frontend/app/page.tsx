"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import NearbyLockerMap from "./NearbyLockerMap";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const RAZORPAY_KEY = process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID ?? "";

type UserSession = { token: string; userId: number; phone: string };
type Booking = {
  public_id: string;
  state: string;
  locker_code: string;
  size: "M" | "L";
  amount_paise: number;
  duration_hours: number;
  razorpay_order_id: string | null;
  expires_at: string;
  ends_at: string | null;
};
type LockerOption = { locker_code: string; size: "M" | "L"; status: "free" | "held" | "offline" };
type LocationLockers = { location_id: number; location_name: string; address: string; items: LockerOption[] };
type RazorpayOptions = {
  key: string;
  amount: number;
  currency: string;
  name: string;
  description: string;
  order_id: string;
  handler: () => void;
  modal: { ondismiss: () => void };
  theme: { color: string };
};
type RazorpayInstance = { open: () => void };
type RazorpayConstructor = new (options: RazorpayOptions) => RazorpayInstance;
class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

declare global {
  interface Window {
    Razorpay?: RazorpayConstructor;
  }
}

async function api<T>(path: string, token?: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers
    }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

async function loadRazorpay(): Promise<void> {
  if (window.Razorpay) return;
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("Could not load Razorpay Checkout."));
    document.body.appendChild(script);
  });
}

function formatRupees(paise: number): string {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(paise / 100);
}

function formatRemainingTime(deadline: string | null, now: number): string {
  if (!deadline) return "Loading booking time…";
  const totalSeconds = Math.max(0, Math.ceil((new Date(deadline).getTime() - now) / 1000));
  if (totalSeconds === 0) return "Time elapsed";
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}`;
}

export default function Home() {
  const [session, setSession] = useState<UserSession | null>(null);
  const [sessionLoaded, setSessionLoaded] = useState(false);
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [debugCode, setDebugCode] = useState("");
  const [stage, setStage] = useState<"map" | "lockers" | "active-locker">("map");
  const [locationId, setLocationId] = useState<number | null>(null);
  const [locationName, setLocationName] = useState("");
  const [locationAddress, setLocationAddress] = useState("");
  const [lockers, setLockers] = useState<LockerOption[]>([]);
  const [selectedLockerCode, setSelectedLockerCode] = useState("");
  const [size, setSize] = useState<"M" | "L">("M");
  const [duration, setDuration] = useState(1);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [now, setNow] = useState(0);
  const bookingRequestRef = useRef<{ body: string; key: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const updateTime = () => setNow(Date.now());
    updateTime();
    const timer = window.setInterval(updateTime, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    try {
      const saved = localStorage.getItem("locker-session");
      if (saved) setSession(JSON.parse(saved) as UserSession);
      const savedBooking = localStorage.getItem("locker-booking");
      if (savedBooking) {
        setBooking(JSON.parse(savedBooking) as Booking);
      }
      if ("serviceWorker" in navigator) {
        void navigator.serviceWorker.register("/sw.js").catch(() => {
          setNotice("App is available online; offline support could not be enabled.");
        });
      }
    } catch {
      localStorage.removeItem("locker-session");
      localStorage.removeItem("locker-booking");
    } finally {
      setSessionLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (!session || !booking) return;
    const refresh = () => {
      void api<Booking>(`/v1/bookings/${booking.public_id}`, session.token)
        .then((latest) => {
          setBooking(latest);
          localStorage.setItem("locker-booking", JSON.stringify(latest));
        })
        .catch(reportError);
    };
    refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => window.clearInterval(timer);
  }, [session, booking?.public_id]);

  useEffect(() => {
    if (!session || !booking || booking.locker_code) return;
    void refreshBooking().catch(reportError);
  }, [session, booking?.public_id]);

  function reportError(reason: unknown) {
    if (reason instanceof ApiError && reason.status === 401) {
      localStorage.removeItem("locker-session");
      setSession(null);
      setError("");
      setNotice("Your sign-in expired (the backend may have restarted). Request a new OTP and sign in again.");
      return;
    }
    if (reason instanceof ApiError && reason.status === 403 && reason.message === "FORBIDDEN") {
      localStorage.removeItem("locker-booking");
      setBooking(null);
      setStage("map");
      setError("");
      setNotice("That saved booking belongs to a different account, so it was cleared. Create or load a booking for this signed-in account.");
      return;
    }
    if (reason instanceof ApiError && reason.status === 409 && reason.message === "NO_LOCKER_AVAILABLE") {
      setSelectedLockerCode("");
      setError("That locker was just taken or is no longer available. Select another available locker code.");
      setNotice("");
      return;
    }
    setError(reason instanceof Error ? reason.message : "Something went wrong. Please try again.");
    setNotice("");
  }

  async function requestOtp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await api<{ debug_code?: string }>("/v1/auth/otp/request", undefined, {
        method: "POST",
        body: JSON.stringify({ phone })
      });
      setDebugCode(response.debug_code ?? "");
      setNotice(response.debug_code ? "Development OTP generated. Enter the code shown below." : "OTP sent.");
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusy(false);
    }
  }

  async function verifyOtp(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await api<{ token: string; user_id: number }>("/v1/auth/otp/verify", undefined, {
        method: "POST",
        body: JSON.stringify({ phone, otp })
      });
      const nextSession = { token: response.token, userId: response.user_id, phone };
      localStorage.setItem("locker-session", JSON.stringify(nextSession));
      setSession(nextSession);
      setNotice("You are signed in.");
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusy(false);
    }
  }

  async function createBooking(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !selectedLockerCode) return;
    setBusy(true);
    setError("");
    const requestBody = { locker_code: selectedLockerCode, duration_hours: duration };
    const bodyKey = JSON.stringify(requestBody);
    if (bookingRequestRef.current?.body !== bodyKey) {
      bookingRequestRef.current = { body: bodyKey, key: crypto.randomUUID() };
    }
    try {
      const created = await api<Booking>("/v1/bookings", session.token, {
        method: "POST",
        headers: { "Idempotency-Key": bookingRequestRef.current.key },
        body: bodyKey
      });
      bookingRequestRef.current = null;
      setBooking(created);
      localStorage.setItem("locker-booking", JSON.stringify(created));
      setStage("map");
      setNotice("Locker held. Complete payment before the hold expires.");
    } catch (reason) {
      reportError(reason);
      if (reason instanceof ApiError && reason.status === 502) {
        bookingRequestRef.current = null;
      }
      if (reason instanceof ApiError && reason.status === 409 && locationId) {
        void api<LocationLockers>(`/v1/locations/${locationId}/lockers`, session.token)
          .then((result) => setLockers(result.items))
          .catch(reportError);
      }
    } finally {
      setBusy(false);
    }
  }

  async function refreshBooking(): Promise<Booking | null> {
    if (!session || !booking) return null;
    const latest = await api<Booking>(`/v1/bookings/${booking.public_id}`, session.token);
    setBooking(latest);
    localStorage.setItem("locker-booking", JSON.stringify(latest));
    return latest;
  }

  async function pay() {
    if (!session || !booking?.razorpay_order_id) return;
    if (!RAZORPAY_KEY) {
      setError("Set NEXT_PUBLIC_RAZORPAY_KEY_ID to enable checkout.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await loadRazorpay();
      if (!window.Razorpay) throw new Error("Razorpay Checkout did not initialize.");
      const checkout = new window.Razorpay({
        key: RAZORPAY_KEY,
        amount: booking.amount_paise,
        currency: "INR",
        name: "Locker",
        description: `${booking.locker_code} · ${booking.duration_hours} hour locker booking`,
        order_id: booking.razorpay_order_id,
        handler: () => {
          setNotice("Payment submitted. Waiting for the verified payment webhook to update your booking.");
          void waitForPayment();
        },
        modal: { ondismiss: () => setNotice("Checkout closed. Your locker hold is still subject to its expiry time.") },
        theme: { color: "#176b55" }
      });
      checkout.open();
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusy(false);
    }
  }

  async function waitForPayment() {
    for (let attempt = 0; attempt < 15; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 2000));
      try {
        const latest = await refreshBooking();
        if (latest?.state === "PAID") {
          setNotice("Payment confirmed. You can now unlock your locker.");
          return;
        }
      } catch (reason) {
        reportError(reason);
        return;
      }
    }
    setNotice("Payment is still processing. Refresh the booking status in a moment.");
  }

  async function unlock() {
    if (!session || !booking) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ cmd_id: string }>(`/v1/bookings/${booking.public_id}/unlock`, session.token, {
        method: "POST"
      });
      setNotice(`Unlock command sent to the locker (command ${result.cmd_id}).`);
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusy(false);
    }
  }

  async function cancelBooking() {
    if (!session || !booking || !window.confirm("Cancel this unpaid locker reservation?")) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ state: string }>(
        `/v1/bookings/${booking.public_id}/cancel`,
        session.token,
        { method: "POST" }
      );
      setBooking(null);
      localStorage.removeItem("locker-booking");
      setStage("map");
      setNotice("Reservation cancelled and locker released.");
    } catch (reason) {
      reportError(reason);
    } finally {
      setBusy(false);
    }
  }

  function signOut() {
    localStorage.removeItem("locker-session");
    localStorage.removeItem("locker-booking");
    setSession(null);
    setBooking(null);
    setStage("map");
    setNotice("");
  }

  function showActiveLocker() {
    if (stage !== "active-locker") {
      setStage("active-locker");
    }
  }

  async function loadLockersAtLocation(nextLocationId: number): Promise<boolean> {
    if (!session) return false;
    setBusy(true);
    setError("");
    setLocationId(nextLocationId);
    setSelectedLockerCode("");
    try {
      const result = await api<LocationLockers>(`/v1/locations/${nextLocationId}/lockers`, session.token);
      setLockers(result.items);
      setLocationName(result.location_name);
      setLocationAddress(result.address);
      setStage("lockers");
      setNotice("");
      return true;
    } catch (reason) {
      reportError(reason);
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!sessionLoaded) {
    return <main className="shell"><p className="nearby-status">Preparing secure sign-in…</p></main>;
  }

  if (!session) {
    return (
      <main className="auth-page shell">
        <header className="topbar">
          <a className="brand" href="/" aria-label="Locker sign in"><span className="brand-mark">L</span> locker</a>
          <span className="secure-label">SECURE STORAGE</span>
        </header>
        <section className="auth-card">
          <p className="eyebrow">WELCOME</p>
          <h1>Sign in to find your locker.</h1>
          <p className="intro">Verify your phone number to see nearby locations and reserve a locker.</p>
          {error && <div className="alert error" role="alert">{error}</div>}
          {notice && <div className="alert success" role="status">{notice}</div>}
          <form onSubmit={requestOtp} className="form-stack">
            <label htmlFor="phone">Phone number</label>
            <input id="phone" type="tel" autoComplete="tel" required minLength={10} value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+91 98765 43210" />
            <button className="button secondary" disabled={busy}>{busy ? "Please wait…" : "Send OTP"}</button>
          </form>
          {debugCode && <div className="dev-code">Development OTP: <strong>{debugCode}</strong></div>}
          <form onSubmit={verifyOtp} className="form-stack otp-form">
            <label htmlFor="otp">6-digit OTP</label>
            <input id="otp" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" maxLength={6} value={otp} onChange={(event) => setOtp(event.target.value)} placeholder="000000" />
            <button className="button" disabled={busy || !otp}>{busy ? "Verifying…" : "Verify & continue"}</button>
          </form>
        </section>
      </main>
    );
  }

  const activeBooking = booking && !["RELEASED", "EXPIRED", "CANCELLED"].includes(booking.state);
  const countdownDeadline = booking?.state === "CREATED"
    ? booking.expires_at
    : booking && ["PAID", "STORED", "OVERSTAY", "DISPUTED"].includes(booking.state)
      ? booking.ends_at
      : null;

  return (
    <main className="shell app-page">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Locker map"><span className="brand-mark">L</span> locker</a>
        <div className="app-user">
          <span>{session.phone}</span>
          {activeBooking && (
            <button type="button" className="text-button" onClick={showActiveLocker} aria-current={stage === "active-locker" ? "page" : undefined}>
              Active locker
            </button>
          )}
          <button className="text-button" onClick={signOut}>Sign out</button>
        </div>
      </header>
      {error && <div className="alert error" role="alert">{error}</div>}
      {notice && <div className="alert success" role="status">{notice}</div>}

      {stage === "map" ? (
        <NearbyLockerMap onArriveAtLocation={loadLockersAtLocation} />
      ) : stage === "lockers" ? (
        <section className="workflow">
          <button type="button" className="text-button" onClick={() => setStage("map")}>← Back to map</button>
          <div className="section-heading">
            <div><p className="eyebrow">YOU’RE AT THE LOCKER LOCATION</p><h1>Choose your locker.</h1><p>{locationName || `Location #${locationId}`} · {locationAddress}</p></div>
          </div>
          <div className="locker-choice-layout">
            <article className="step-card locker-choice-card">
              <h3>Select a size</h3>
              <div className="size-choice">
                <button type="button" className={size === "M" ? "selected" : ""} onClick={() => { setSize("M"); setSelectedLockerCode(""); }}>
                  Medium <span>{formatRupees(4000)} / hour</span>
                </button>
                <button type="button" className={size === "L" ? "selected" : ""} onClick={() => { setSize("L"); setSelectedLockerCode(""); }}>
                  Large <span>{formatRupees(6000)} / hour</span>
                </button>
              </div>
              <h3>Available {size === "M" ? "medium" : "large"} lockers</h3>
              {busy ? <p>Loading locker inventory…</p> : (
                <div className="locker-code-list">
                  {lockers.filter((locker) => locker.size === size).map((locker) => (
                    <button
                      type="button"
                      key={locker.locker_code}
                      disabled={locker.status !== "free"}
                      className={`${selectedLockerCode === locker.locker_code ? "selected" : ""} ${locker.status !== "free" ? "unavailable" : ""}`}
                      onClick={() => setSelectedLockerCode(locker.locker_code)}
                    >
                      <strong>{locker.locker_code}</strong>
                      <span>{locker.status === "free" ? "Available" : locker.status === "held" ? "Reserved" : "Offline"}</span>
                    </button>
                  ))}
                </div>
              )}
              {!busy && lockers.filter((locker) => locker.size === size && locker.status === "free").length === 0 && (
                <p className="nearby-unavailable">No {size === "M" ? "medium" : "large"} lockers are available here. Try another size or location.</p>
              )}
              <form onSubmit={createBooking} className="form-stack">
                <label htmlFor="duration">Duration</label>
                <div className="duration-control">
                  <button type="button" aria-label="Decrease duration" onClick={() => setDuration(Math.max(1, duration - 1))}>−</button>
                  <span>{duration} {duration === 1 ? "hour" : "hours"}</span>
                  <button type="button" aria-label="Increase duration" onClick={() => setDuration(Math.min(72, duration + 1))}>+</button>
                </div>
                <div className="price-row"><span>Estimated total</span><strong>{formatRupees((size === "M" ? 4000 : 6000) * duration)}</strong></div>
                <button className="button" disabled={busy || !selectedLockerCode}>{busy ? "Reserving…" : "Continue to payment"}</button>
              </form>
            </article>
          </div>
        </section>
      ) : (
        <section className="workflow payment-page active-locker-page">
          <button type="button" className="text-button" onClick={() => setStage("map")}>← Back to map</button>
          <div className="section-heading">
            <div>
              <p className="eyebrow">{activeBooking ? "ACTIVE LOCKER" : "BOOKING"}</p>
              <h1>{activeBooking ? "Your locker details." : booking?.state === "EXPIRED" ? "Reservation expired." : "Locker booking."}</h1>
            </div>
          </div>
          {booking ? (
            <article className="step-card payment-card">
              <div className="booking-id">BOOKING <strong>{booking.public_id.slice(0, 8).toUpperCase()}</strong></div>
              <div className="booking-state"><span className="state-dot" />{booking.state}</div>
              <div className="booking-detail"><span>Locker</span><strong>{booking.locker_code}</strong></div>
              <div className="booking-detail"><span>Size</span><strong>{booking.size === "M" ? "Medium" : "Large"}</strong></div>
              <div className="booking-detail"><span>Duration</span><strong>{booking.duration_hours} {booking.duration_hours === 1 ? "hour" : "hours"}</strong></div>
              <div className="booking-detail"><span>Total</span><strong>{formatRupees(booking.amount_paise)}</strong></div>
              {activeBooking && (
                <>
                  <div className="booking-countdown" role="timer" aria-label={`Time remaining: ${formatRemainingTime(countdownDeadline, now)}`}>
                    <span>Time remaining</span>
                    <strong>{formatRemainingTime(countdownDeadline, now)}</strong>
                  </div>
                  {countdownDeadline && (
                    <div className="booking-detail">
                      <span>{booking.state === "CREATED" ? "Reservation hold expires" : "Booking ends"}</span>
                      <strong>{new Date(countdownDeadline).toLocaleString()}</strong>
                    </div>
                  )}
                </>
              )}
              {(booking.state === "CREATED" || booking.state === "EXPIRED") && (
                <button className="button" onClick={pay} disabled={busy || booking.state === "EXPIRED" || !booking.razorpay_order_id}>
                  {busy ? "Opening checkout…" : "Pay securely"}
                </button>
              )}
              {booking.state === "CREATED" && (
                <button className="button secondary" onClick={() => void cancelBooking()} disabled={busy}>
                  Cancel reservation
                </button>
              )}
              {["PAID", "STORED", "OVERSTAY"].includes(booking.state) && (
                <button className="button" onClick={unlock} disabled={busy}>
                  {busy ? "Sending command…" : "Unlock locker"}
                </button>
              )}
              <button className="text-button refresh-button" onClick={() => void refreshBooking().catch(reportError)}>Refresh booking status</button>
              {!RAZORPAY_KEY && <p className="config-note">Checkout needs a Razorpay key configured in the frontend environment.</p>}
            </article>
          ) : (
            <>
              <p className="empty-state">No booking is available. Return to the map to choose a locker.</p>
              <button type="button" className="button" onClick={() => setStage("map")}>Open map</button>
            </>
          )}
        </section>
      )}
      <footer><span>LOCKER · STORAGE MADE SIMPLE</span><span>Need help? Contact your location operator.</span></footer>
    </main>
  );
}
