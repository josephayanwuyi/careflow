import { describe, expect, it, vi } from "vitest";

import {
  getNotificationPermission,
  notifyPatientCalled,
  notifyPatientNext,
  requestNotificationPermission,
  sendBrowserNotification,
} from "./browserNotifications";

describe("browserNotifications", () => {
  it("reports permission state when Notification is present", () => {
    const originalNotification = window.Notification;

    // Mock Notification API
    const mockNotification = vi.fn() as unknown as typeof Notification;
    Object.defineProperty(mockNotification, "permission", {
      value: "granted",
      writable: true,
      configurable: true,
    });
    mockNotification.requestPermission = vi.fn().mockResolvedValue("granted");

    window.Notification = mockNotification;

    expect(getNotificationPermission()).toBe("granted");

    window.Notification = originalNotification;
  });

  it("sends browser notification when granted", () => {
    const originalNotification = window.Notification;

    const mockConstructor = vi.fn();
    Object.defineProperty(mockConstructor, "permission", {
      value: "granted",
      writable: true,
      configurable: true,
    });

    window.Notification = mockConstructor as unknown as typeof Notification;

    notifyPatientCalled(42, "Dr. Sarah Smith");
    expect(mockConstructor).toHaveBeenCalledWith(
      "CareFlow: It's your turn!",
      expect.objectContaining({
        body: "Ticket #42: Please proceed to Dr. Sarah Smith now.",
        tag: "careflow-called",
      }),
    );

    notifyPatientNext(43, "Dr. Sarah Smith");
    expect(mockConstructor).toHaveBeenCalledWith(
      "CareFlow: You are next in line!",
      expect.objectContaining({
        body: "Ticket #43: Please stay near the waiting area for Dr. Sarah Smith.",
        tag: "careflow-next",
      }),
    );

    window.Notification = originalNotification;
  });

  it("does not throw or send when permission is denied", () => {
    const originalNotification = window.Notification;

    const mockConstructor = vi.fn();
    Object.defineProperty(mockConstructor, "permission", {
      value: "denied",
      writable: true,
      configurable: true,
    });

    window.Notification = mockConstructor as unknown as typeof Notification;

    const res = sendBrowserNotification("Test Title");
    expect(res).toBeNull();
    expect(mockConstructor).not.toHaveBeenCalled();

    window.Notification = originalNotification;
  });

  it("requests permission properly", async () => {
    const originalNotification = window.Notification;

    const mockRequestPermission = vi.fn().mockResolvedValue("granted");
    const mockConstructor = vi.fn();
    Object.defineProperty(mockConstructor, "requestPermission", {
      value: mockRequestPermission,
      writable: true,
      configurable: true,
    });

    window.Notification = mockConstructor as unknown as typeof Notification;

    const perm = await requestNotificationPermission();
    expect(perm).toBe("granted");
    expect(mockRequestPermission).toHaveBeenCalled();

    window.Notification = originalNotification;
  });
});
