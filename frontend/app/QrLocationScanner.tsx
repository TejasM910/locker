"use client";

import { useEffect, useRef, useState } from "react";
import { BrowserMultiFormatReader } from "@zxing/browser";

export default function QrLocationScanner({ onScan, onClose }: { onScan: (value: string) => void; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const onScanRef = useRef(onScan);
  const [error, setError] = useState("");

  useEffect(() => {
    onScanRef.current = onScan;
  }, [onScan]);

  useEffect(() => {
    const reader = new BrowserMultiFormatReader();
    const video = videoRef.current;
    if (!video) {
      setError("Camera preview could not be initialized.");
      return;
    }
    let disposed = false;
    let stop: (() => void) | undefined;

    reader.decodeFromVideoDevice(undefined, video, (result, _decodeError, controls) => {
      stop = controls.stop.bind(controls);
      if (result && !disposed) {
        stop();
        onScanRef.current(result.getText());
      }
    }).then((controls) => {
      stop = controls.stop.bind(controls);
      if (disposed) stop();
    }).catch((reason: unknown) => {
      if (!disposed) setError(reason instanceof Error ? reason.message : "Camera access failed.");
    });

    return () => {
      disposed = true;
      stop?.();
    };
  }, []);

  return (
    <div className="scanner-backdrop" role="dialog" aria-modal="true" aria-labelledby="scanner-title">
      <div className="scanner-panel">
        <div className="scanner-heading"><h3 id="scanner-title">Scan location QR</h3><button className="text-button" onClick={onClose}>Close</button></div>
        <p>Point your camera at a location QR code.</p>
        <video ref={videoRef} className="scanner-video" muted playsInline />
        {error && <div className="alert error" role="alert">{error}. You can enter the location ID manually.</div>}
      </div>
    </div>
  );
}
