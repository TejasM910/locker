"use client";

import { FormEvent, useEffect, useState } from "react";
import QrLocationScanner from "./QrLocationScanner";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const RAZORPAY_KEY = process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID ?? "";

type UserSession = { token: string; userId: number; phone: string };
type Booking = {
  public_id: string;
  state: string;
  amount_paise: number;
  duration_hours: number;
  razorpay_order_id: string | null;
  expires_at: string;
};
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

export default function Home() {
  const [session, setSession] = useState<UserSession | null>(null);
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [debugCode, setDebugCode] = useState("");
  const [locationId, setLocationId] = useState("");
  const [size, setSize] = useState<"M" | "L">("M");
  const [duration, setDuration] = useState(1);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [booking, setBooking] = useState<Booking | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    try {
      const saved = localStorage.getItem("locker-session");
      if (saved) setSession(JSON.parse(saved) as UserSession);
      const savedBooking = localStorage.getItem("locker-booking");
      if (savedBooking) setBooking(JSON.parse(savedBooking) as Booking);
      if ("serviceWorker" in navigator) {
        void navigator.serviceWorker.register("/sw.js").catch(() => {
          setNotice("App is available online; offline support could not be enabled.");
        });
      }
    } catch {
      localStorage.removeItem("locker-session");
      localStorage.removeItem("locker-booking");
    }
  }, []);

  useEffect(() => {
    if (!session || !booking) return;
    const timer = window.setInterval(() => {
      void api<Booking>(`/v1/bookings/${booking.public_id}`, session.token)
        .then((latest) => {
          setBooking(latest);
          localStorage.setItem("locker-booking", JSON.stringify(latest));
        })
        .catch(reportError);
    }, 10000);
    return () => window.clearInterval(timer);
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
      setError("");
      setNotice("That saved booking belongs to a different account, so it was cleared. Create or load a booking for this signed-in account.");
      return;
    }
    if (reason instanceof ApiError && reason.status === 409 && reason.message === "NO_LOCKER_AVAILABLE") {
      setError("No free locker matches this location and size. Check the location ID and available locker inventory, or try another size.");
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
    if (!session) return;
    setBusy(true);
    setError("");
    try {
      const created = await api<Booking>("/v1/bookings", session.token, {
        method: "POST",
        body: JSON.stringify({ location_id: Number(locationId), size, duration_hours: duration })
      });
      setBooking(created);
      localStorage.setItem("locker-booking", JSON.stringify(created));
      setNotice("Locker held. Complete payment before the hold expires.");
    } catch (reason) {
      reportError(reason);
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
        description: `${booking.duration_hours} hour locker booking`,
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

  function signOut() {
    localStorage.removeItem("locker-session");
    localStorage.removeItem("locker-booking");
    setSession(null);
    setBooking(null);
    setNotice("Signed out.");
  }

  function applyScannedLocation(value: string) {
    const directId = value.trim().match(/^\d+$/)?.[0];
    let locationFromUrl = "";
    try {
      locationFromUrl = new URL(value).searchParams.get("location_id") ?? "";
    } catch {
      const parameter = value.match(/(?:^|[?&])location_id=(\d+)/i);
      locationFromUrl = parameter?.[1] ?? "";
    }
    const scannedId = directId ?? locationFromUrl;
    if (!scannedId || Number(scannedId) < 1) {
      setError("This QR code does not contain a valid location ID. Enter it manually instead.");
      return;
    }
    setLocationId(scannedId);
    setScannerOpen(false);
    setError("");
    setNotice(`Location ${scannedId} scanned. Choose your locker details to continue.`);
  }

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Locker home"><span className="brand-mark">L</span> locker</a>
        {session ? <button className="text-button" onClick={signOut}>Sign out</button> : <span className="secure-label">SECURE STORAGE</span>}
      </header>

      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">YOUR DAY, UNBURDENED</p>
          <h1>Drop your bags.<br /><em>Keep exploring.</em></h1>
          <p className="intro">Find a spot for your things and get straight back to the good part of your day.</p>
          <div className="hero-note"><span className="status-dot" /> Secure booking · Pay online · Unlock from your phone</div>
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="sun" />
          <div className="arch"><div className="locker-door"><span /><span /><span /></div></div>
          <div className="plant plant-one">✳</div><div className="plant plant-two">✳</div>
        </div>
      </section>

      <section className="workflow">
        <div className="section-heading">
          <div><p className="eyebrow">THREE EASY STEPS</p><h2>Make room for more.</h2></div>
          {session && <span className="signed-in">Signed in · {session.phone}</span>}
        </div>

        {error && <div className="alert error" role="alert">{error}</div>}
        {notice && <div className="alert success" role="status">{notice}</div>}

        {!session ? (
          <div className="steps-grid">
            <article className="step-card">
              <span className="step-number">01</span><h3>Verify your number</h3>
              <p>We’ll use your phone number to secure your booking.</p>
              <form onSubmit={requestOtp} className="form-stack">
                <label htmlFor="phone">Phone number</label>
                <input id="phone" type="tel" autoComplete="tel" required minLength={10} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" />
                <button className="button secondary" disabled={busy}>{busy ? "Please wait…" : "Send OTP"}</button>
              </form>
              {debugCode && <div className="dev-code">Development OTP: <strong>{debugCode}</strong></div>}
              <form onSubmit={verifyOtp} className="form-stack otp-form">
                <label htmlFor="otp">6-digit OTP</label>
                <input id="otp" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" maxLength={6} value={otp} onChange={(e) => setOtp(e.target.value)} placeholder="000000" />
                <button className="button" disabled={busy || !otp}>{busy ? "Verifying…" : "Verify & continue"}</button>
              </form>
            </article>
            <article className="step-card info-card"><span className="step-number">02</span><h3>Choose your locker</h3><p>Pick a location, locker size, and how long you need it. We’ll hold your locker while you pay.</p><div className="mini-illustration">▣</div></article>
            <article className="step-card info-card"><span className="step-number">03</span><h3>Pay & unlock</h3><p>Pay securely with Razorpay, then send an unlock command straight from your booking.</p><div className="mini-illustration">↗</div></article>
          </div>
        ) : (
          <div className="booking-layout">
            <article className="step-card booking-form">
              <span className="step-number">02</span><h3>Choose your locker</h3>
              <p>Scan the location QR or enter the ID provided by your locker operator.</p>
              <button type="button" className="button secondary scan-button" onClick={() => setScannerOpen(true)}>Scan location QR</button>
              <form onSubmit={createBooking} className="form-stack">
                <label htmlFor="location">Location ID</label>
                <input id="location" type="number" min="1" required value={locationId} onChange={(e) => setLocationId(e.target.value)} placeholder="e.g. 1" />
                <label htmlFor="size">Locker size</label>
                <select id="size" value={size} onChange={(e) => setSize(e.target.value as "M" | "L")}><option value="M">Medium · {formatRupees(4000)} / hour</option><option value="L">Large · {formatRupees(6000)} / hour</option></select>
                <label htmlFor="duration">Duration</label>
                <div className="duration-control"><button type="button" aria-label="Decrease duration" onClick={() => setDuration(Math.max(1, duration - 1))}>−</button><span>{duration} {duration === 1 ? "hour" : "hours"}</span><button type="button" aria-label="Increase duration" onClick={() => setDuration(Math.min(72, duration + 1))}>+</button></div>
                <div className="price-row"><span>Estimated total</span><strong>{formatRupees((size === "M" ? 4000 : 6000) * duration)}</strong></div>
                <button className="button" disabled={busy}>{busy ? "Reserving…" : "Find & reserve a locker"}</button>
              </form>
            </article>
            <article className="step-card current-booking">
              <span className="step-number">03</span><h3>Your booking</h3>
              {booking ? (
                <>
                  <div className="booking-id">BOOKING <strong>{booking.public_id.slice(0, 8).toUpperCase()}</strong></div>
                  <div className="booking-state"><span className="state-dot" />{booking.state}</div>
                  <div className="booking-detail"><span>Duration</span><strong>{booking.duration_hours} {booking.duration_hours === 1 ? "hour" : "hours"}</strong></div>
                  <div className="booking-detail"><span>Total</span><strong>{formatRupees(booking.amount_paise)}</strong></div>
                  <div className="booking-detail"><span>Hold expires</span><strong>{new Date(booking.expires_at).toLocaleTimeString()}</strong></div>
                  {(booking.state === "CREATED" || booking.state === "EXPIRED") && <button className="button" onClick={pay} disabled={busy || booking.state === "EXPIRED" || !booking.razorpay_order_id}>{busy ? "Opening checkout…" : "Pay securely"}</button>}
                  {["PAID", "STORED", "OVERSTAY"].includes(booking.state) && <button className="button" onClick={unlock} disabled={busy}>{busy ? "Sending command…" : "Unlock locker"}</button>}
                  <button className="text-button refresh-button" onClick={() => void refreshBooking().catch(reportError)}>Refresh booking status</button>
                </>
              ) : <p className="empty-state">Your reservation will appear here once you choose a locker.</p>}
              {!RAZORPAY_KEY && <p className="config-note">Checkout needs a Razorpay key configured in the frontend environment.</p>}
            </article>
          </div>
        )}
      </section>

      {scannerOpen && <QrLocationScanner onScan={applyScannedLocation} onClose={() => setScannerOpen(false)} />}
      <footer><span>LOCKER · STORAGE MADE SIMPLE</span><span>Need help? Contact your location operator.</span></footer>
    </main>
  );
}
