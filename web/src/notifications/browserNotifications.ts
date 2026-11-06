/**
 * Browser push notifications and Web Audio synthesized chimes.
 *
 * Provides:
 * 1. Web Notification API integration (native OS notifications for desktop/mobile).
 * 2. Synthesized Web Audio chimes (zero external audio files needed).
 * 3. Graceful fallback when notifications or audio are unsupported or blocked.
 */

export type NotificationPermissionState = "default" | "granted" | "denied" | "unsupported";

export function getNotificationPermission(): NotificationPermissionState {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "unsupported";
  }
  return Notification.permission as NotificationPermissionState;
}

export async function requestNotificationPermission(): Promise<NotificationPermissionState> {
  if (typeof window === "undefined" || !("Notification" in window)) {
    return "unsupported";
  }
  try {
    const perm = await Notification.requestPermission();
    return perm as NotificationPermissionState;
  } catch {
    return "denied";
  }
}

/**
 * Synthesize a musical chime using the Web Audio API.
 * Does not require downloading any external sound assets.
 */
function playChime(frequencies: number[], durations: number[] = [0.15, 0.15, 0.3]): void {
  try {
    if (typeof window === "undefined") return;
    const AudioCtx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;

    const ctx = new AudioCtx();
    let startTime = ctx.currentTime + 0.05;

    frequencies.forEach((freq, idx) => {
      const dur = durations[idx] ?? 0.2;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();

      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, startTime);

      // Smooth attack and decay envelope
      gain.gain.setValueAtTime(0.001, startTime);
      gain.gain.exponentialRampToValueAtTime(0.3, startTime + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.001, startTime + dur);

      osc.connect(gain);
      gain.connect(ctx.destination);

      osc.start(startTime);
      osc.stop(startTime + dur);

      startTime += dur * 0.85;
    });
  } catch {
    // Ignore audio autoplay restrictions or uninitialized audio
  }
}

/** Alert chime when the patient is called into the consultation room. (C5 -> E5 -> G5) */
export function playCallingChime(): void {
  playChime([523.25, 659.25, 783.99], [0.18, 0.18, 0.4]);
}

/** Gentle chime when the patient is next in line. (A4 -> C#5) */
export function playNextInLineChime(): void {
  playChime([440.0, 554.37], [0.18, 0.3]);
}

/**
 * Dispatch a native OS/browser notification if permission is granted.
 */
export function sendBrowserNotification(
  title: string,
  options?: NotificationOptions & { playSound?: "called" | "next" },
): Notification | null {
  if (options?.playSound === "called") {
    playCallingChime();
  } else if (options?.playSound === "next") {
    playNextInLineChime();
  }

  if (getNotificationPermission() !== "granted") {
    return null;
  }

  try {
    return new Notification(title, {
      icon: "/favicon.ico",
      badge: "/favicon.ico",
      ...options,
    });
  } catch {
    return null;
  }
}

/**
 * Notify that the patient's ticket is being called.
 */
export function notifyPatientCalled(ticketNumber: number, doctorName: string): void {
  sendBrowserNotification("CareFlow: It's your turn!", {
    body: `Ticket #${ticketNumber}: Please proceed to ${doctorName} now.`,
    tag: "careflow-called",
    playSound: "called",
  });
}

/**
 * Notify that the patient is next in line.
 */
export function notifyPatientNext(ticketNumber: number, doctorName: string): void {
  sendBrowserNotification("CareFlow: You are next in line!", {
    body: `Ticket #${ticketNumber}: Please stay near the waiting area for ${doctorName}.`,
    tag: "careflow-next",
    playSound: "next",
  });
}
