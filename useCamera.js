/**
 * useCamera  —  webcam lifecycle and throttled frame capture.
 *
 * WHY A HOOK
 * ----------
 * Camera handling has real lifecycle hazards: a MediaStream not stopped on
 * unmount leaves the camera light on and the device locked against other apps.
 * Isolating it here means every consumer gets the cleanup for free.
 *
 * *** THE SECURE-CONTEXT TRAP -- read this before demo day ***
 * getUserMedia only works in a secure context. That means HTTPS, or
 * `http://localhost`. It FAILS on `http://192.168.x.x`.
 *
 * So the moment you demo from a phone or a second laptop on the college wifi,
 * the camera silently dies. This is risk R-2 from the design document, and it
 * has ended more live demos than any other item on that list.
 *
 * The hook detects the situation and returns a specific, actionable message
 * rather than a generic failure, because "NotAllowedError" tells a person
 * nothing.
 *
 * FRAME THROTTLING
 * ----------------
 * We capture at ~2 fps, not 30. Consecutive frames of a person standing still
 * are nearly identical, so 30 fps would send fifteen times the data and
 * fifteen times the server inference cost for no additional recognition.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export function useCamera({ width = 1280, height = 720 } = {}) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const canvasRef = useRef(null);

  const [active, setActive] = useState(false);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);

  const start = useCallback(async () => {
    setError(null);
    setStarting(true);

    // Check the secure-context requirement BEFORE asking for the camera, so
    // the message names the real cause instead of a permissions error.
    if (!window.isSecureContext && window.location.hostname !== "localhost") {
      setError(
        "The camera needs a secure connection. Open this page at http://localhost, " +
          "or serve it over HTTPS."
      );
      setStarting(false);
      return false;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser does not support camera access. Try Chrome or Edge.");
      setStarting(false);
      return false;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: width },
          height: { ideal: height },
          facingMode: "user",
        },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setActive(true);
      return true;
    } catch (err) {
      // Each branch names what the person can actually do about it.
      const messages = {
        NotAllowedError:
          "Camera access was blocked. Allow it in the address bar, then start again.",
        NotFoundError: "No camera found. Connect one and reload the page.",
        NotReadableError:
          "The camera is in use by another app. Close Zoom, Teams or OBS and try again.",
      };
      setError(messages[err.name] || `Could not start the camera: ${err.message}`);
      return false;
    } finally {
      setStarting(false);
    }
  }, [width, height]);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setActive(false);
  }, []);

  /**
   * Grab the current video frame as a JPEG Blob.
   *
   * Quality 0.8 is deliberate: visually near-lossless, roughly a third the
   * bytes of quality 1.0. At 2 fps over a classroom wifi that difference is
   * the whole bandwidth budget.
   */
  const capture = useCallback(
    (quality = 0.8) =>
      new Promise((resolve) => {
        const video = videoRef.current;
        if (!video || video.readyState < 2) return resolve(null);

        if (!canvasRef.current) canvasRef.current = document.createElement("canvas");
        const canvas = canvasRef.current;
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;

        const ctx = canvas.getContext("2d");
        ctx.drawImage(video, 0, 0);
        canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality);
      }),
    []
  );

  // Stop the stream when the component unmounts. Without this, navigating away
  // leaves the camera light on -- which users reasonably read as spyware.
  useEffect(() => stop, [stop]);

  return { videoRef, start, stop, capture, active, error, starting };
}
