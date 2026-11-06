import { patientApi } from "../api/patient";
import type { NotificationItem } from "../api/types";
import { ErrorBox, Spinner } from "../components/ui";
import { formatDateTime } from "../format";
import type { PatientData } from "./usePatientData";

interface Props {
  data: PatientData;
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
}

export function NotificationsPage({ data, run, busy }: Props) {
  if (data.loading && data.notifications.length === 0) {
    return <Spinner label="Loading notifications…" />;
  }

  const markAllRead = () =>
    run(() => patientApi.markAllRead(), "All notifications marked as read.");

  const markOneRead = (n: NotificationItem) => {
    if (n.read_at) return;
    void run(() => patientApi.markRead(n.id), "Notification marked as read.");
  };

  return (
    <div className="stack">
      {data.error != null && <ErrorBox error={data.error} onRetry={() => void data.reload()} />}

      <section className="card stack" aria-labelledby="notif-heading">
        <div className="card-header" style={{ flexWrap: "wrap", gap: "0.5rem" }}>
          <h2 id="notif-heading" style={{ margin: 0 }}>
            Notifications {data.unreadCount > 0 ? `(${data.unreadCount} unread)` : ""}
          </h2>
          {data.unreadCount > 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={markAllRead}
              style={{ minHeight: "38px", padding: "0.4rem 0.75rem", fontSize: "0.9rem" }}
            >
              Mark all as read
            </button>
          )}
        </div>

        {data.notifications.length === 0 ? (
          <p className="muted" style={{ padding: "1.5rem 0", textAlign: "center" }}>
            You have no notifications at this time.
          </p>
        ) : (
          <ul className="plain-list" aria-label="Notifications list">
            {data.notifications.map((n) => {
              const isUnread = !n.read_at;
              return (
                <li
                  key={n.id}
                  style={{
                    display: "flex",
                    gap: "0.75rem",
                    alignItems: "flex-start",
                    padding: "0.85rem 0",
                    borderBottom: "1px solid #e3e8ef",
                  }}
                >
                  <span
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: "50%",
                      background: isUnread ? "var(--blue)" : "transparent",
                      marginTop: 6,
                      flexShrink: 0,
                    }}
                    aria-label={isUnread ? "Unread" : "Read"}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "baseline",
                        flexWrap: "wrap",
                        gap: "0.25rem",
                      }}
                    >
                      <p style={{ margin: 0, fontWeight: isUnread ? 700 : 500, wordBreak: "break-word" }}>
                        {n.title}
                      </p>
                      <span className="small muted" style={{ fontSize: "0.8rem", whiteSpace: "nowrap" }}>
                        {formatDateTime(n.created_at, "UTC")}
                      </span>
                    </div>
                    <p
                      className="small"
                      style={{
                        margin: "0.3rem 0 0",
                        color: isUnread ? "var(--text)" : "var(--muted)",
                        wordBreak: "break-word",
                        lineHeight: 1.4,
                      }}
                    >
                      {n.body}
                    </p>
                    {isUnread && (
                      <div style={{ marginTop: "0.5rem" }}>
                        <button
                          type="button"
                          style={{ fontSize: "0.825rem", padding: "0.3rem 0.75rem", minHeight: "36px" }}
                          disabled={busy}
                          onClick={() => markOneRead(n)}
                        >
                          Mark read
                        </button>
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
