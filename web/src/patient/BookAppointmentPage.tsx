import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { patientApi } from "../api/patient";
import type { Department, DoctorAvailability, DoctorPublic, Hospital, Slot } from "../api/types";
import { ErrorBox, Spinner, StatusBadge } from "../components/ui";
import { formatTime, todayIn } from "../format";

interface Props {
  run: (action: () => Promise<unknown>, success: string) => Promise<void>;
  busy: boolean;
}

export function BookAppointmentPage({ run, busy }: Props) {
  const navigate = useNavigate();

  // Data
  const [hospitals, setHospitals] = useState<Hospital[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [doctors, setDoctors] = useState<DoctorPublic[]>([]);
  const [availabilities, setAvailabilities] = useState<Record<string, DoctorAvailability>>({});
  const [slots, setSlots] = useState<Slot[]>([]);

  // Selections
  const [selectedHospitalId, setSelectedHospitalId] = useState("");
  const [selectedDeptId, setSelectedDeptId] = useState("");
  const [selectedDoctorId, setSelectedDoctorId] = useState("");
  const [selectedDate, setSelectedDate] = useState("");
  const [selectedSlot, setSelectedSlot] = useState<Slot | null>(null);
  const [reasonForVisit, setReasonForVisit] = useState("");

  // Loading states
  const [loadingHospitals, setLoadingHospitals] = useState(true);
  const [loadingDoctors, setLoadingDoctors] = useState(false);
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const selectedHospital = hospitals.find((h) => h.id === selectedHospitalId);
  const selectedDoctor = doctors.find((d) => d.id === selectedDoctorId);

  // Load hospitals on mount
  useEffect(() => {
    patientApi
      .hospitals()
      .then((res) => {
        setHospitals(res);
        if (res.length > 0 && !selectedHospitalId) {
          setSelectedHospitalId(res[0].id);
        }
      })
      .catch((err) => setError(err))
      .finally(() => setLoadingHospitals(false));
  }, []);

  // When hospital changes, load departments and doctors
  useEffect(() => {
    if (!selectedHospitalId) return;
    setLoadingDoctors(true);
    setSelectedDeptId("");
    setSelectedDoctorId("");
    setSelectedSlot(null);
    setSlots([]);

    Promise.all([
      patientApi.departments(selectedHospitalId),
      patientApi.doctors(selectedHospitalId),
      patientApi.doctorAvailability(selectedHospitalId).catch(() => [] as DoctorAvailability[]),
    ])
      .then(([depts, docs, avails]) => {
        setDepartments(depts);
        setDoctors(docs);
        const map: Record<string, DoctorAvailability> = {};
        for (const a of avails) map[a.doctor_id] = a;
        setAvailabilities(map);
      })
      .catch((err) => setError(err))
      .finally(() => setLoadingDoctors(false));
  }, [selectedHospitalId]);

  // When hospital changes or date is blank, default date to today in hospital timezone
  useEffect(() => {
    if (selectedHospital && !selectedDate) {
      setSelectedDate(todayIn(selectedHospital.timezone));
    }
  }, [selectedHospital, selectedDate]);

  // Filter doctors by selected department if any
  const filteredDoctors = selectedDeptId
    ? doctors.filter((d) => d.department_id === selectedDeptId)
    : doctors;

  // When doctor or date changes, load slots
  useEffect(() => {
    if (!selectedDoctorId || !selectedDate) {
      setSlots([]);
      setSelectedSlot(null);
      return;
    }
    setLoadingSlots(true);
    setSelectedSlot(null);
    patientApi
      .doctorSlots(selectedDoctorId, selectedDate)
      .then((res) => {
        setSlots(res.slots);
      })
      .catch((err) => setError(err))
      .finally(() => setLoadingSlots(false));
  }, [selectedDoctorId, selectedDate]);

  const submitRequest = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedDoctorId || !selectedSlot) return;

    await run(
      () =>
        patientApi.requestAppointment({
          doctor_id: selectedDoctorId,
          department_id: selectedDoctor?.department_id ?? null,
          start_at: selectedSlot.start_at,
          reason_for_visit: reasonForVisit.trim() || null,
        }),
      "Appointment request submitted! Hospital reception will review and confirm your slot.",
    );

    navigate("/patient/appointments");
  };

  if (loadingHospitals) return <Spinner label="Loading hospital directory…" />;

  return (
    <div className="stack">
      {error != null && <ErrorBox error={error} onRetry={() => setError(null)} />}

      <section className="card stack" aria-labelledby="book-heading">
        <h2 id="book-heading">Book an Appointment</h2>
        <p className="muted">Find a doctor, choose an available time slot, and submit your request.</p>

        <form onSubmit={submitRequest} className="stack">
          {/* 1. Hospital Selector */}
          <div className="field">
            <label htmlFor="hospital-select">Hospital</label>
            <select
              id="hospital-select"
              value={selectedHospitalId}
              onChange={(e) => setSelectedHospitalId(e.target.value)}
            >
              {hospitals.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name} ({h.code})
                </option>
              ))}
            </select>
          </div>

          {/* 2. Department Filter */}
          {departments.length > 0 && (
            <div className="field">
              <label htmlFor="dept-select">Department (optional filter)</label>
              <select
                id="dept-select"
                value={selectedDeptId}
                onChange={(e) => {
                  setSelectedDeptId(e.target.value);
                  setSelectedDoctorId("");
                  setSelectedSlot(null);
                }}
              >
                <option value="">All Departments</option>
                {departments.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* 3. Doctor Selector */}
          <div className="field">
            <label>Select Doctor</label>
            {loadingDoctors ? (
              <Spinner label="Loading doctors…" />
            ) : filteredDoctors.length === 0 ? (
              <p className="muted">No doctors found for the selected department.</p>
            ) : (
              <div
                className="doctor-grid"
                aria-label="Available Doctors"
              >
                {filteredDoctors.map((doc) => {
                  const avail = availabilities[doc.id];
                  const isSelected = selectedDoctorId === doc.id;
                  return (
                    <button
                      key={doc.id}
                      type="button"
                      className="card"
                      style={{
                        cursor: "pointer",
                        border: isSelected ? "2px solid var(--blue)" : "1px solid var(--border)",
                        background: isSelected ? "#f0f7ff" : "white",
                        padding: "0.85rem",
                        textAlign: "left",
                        width: "100%",
                        display: "block",
                      }}
                      aria-pressed={isSelected}
                      onClick={() => {
                        setSelectedDoctorId(doc.id);
                        setSelectedSlot(null);
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "0.5rem", flexWrap: "wrap" }}>
                        <p style={{ margin: 0, fontWeight: 700, wordBreak: "break-word" }}>{doc.display_name}</p>
                        {avail && (
                          <StatusBadge
                            status={avail.status === "checked_in" ? "checked_in_doctor" : avail.status}
                            label={avail.public_status.replace(/_/g, " ")}
                          />
                        )}
                      </div>
                      <p className="small muted" style={{ margin: "0.25rem 0" }}>
                        {doc.specialty || doc.department_name}
                      </p>
                      {isSelected && (
                        <span className="badge badge-approved" style={{ marginTop: "0.5rem" }}>
                          ✓ Selected
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* 4. Date Picker */}
          {selectedDoctorId && (
            <div className="field">
              <label htmlFor="date-select">Appointment Date</label>
              <input
                id="date-select"
                type="date"
                min={selectedHospital ? todayIn(selectedHospital.timezone) : undefined}
                value={selectedDate}
                required
                onChange={(e) => {
                  setSelectedDate(e.target.value);
                  setSelectedSlot(null);
                }}
              />
            </div>
          )}

          {/* 5. Slots Grid */}
          {selectedDoctorId && selectedDate && (
            <div className="field">
              <label>Available Slots for {selectedDate}</label>
              {loadingSlots ? (
                <Spinner label="Checking slot schedule…" />
              ) : slots.length === 0 ? (
                <p className="muted">No appointment slots scheduled for this date. Please choose another date.</p>
              ) : (
                <div
                  className="slots-grid"
                  role="radiogroup"
                  aria-label="Appointment Slots"
                >
                  {slots.map((slot) => {
                    const isSelected = selectedSlot?.start_at === slot.start_at;
                    const timeLabel = formatTime(slot.start_at, selectedHospital?.timezone ?? "UTC");

                    return (
                      <button
                        key={slot.start_at}
                        type="button"
                        disabled={!slot.available || busy}
                        className={isSelected ? "primary" : undefined}
                        style={{
                          minHeight: 44,
                          fontWeight: isSelected ? 700 : 500,
                        }}
                        role="radio"
                        aria-checked={isSelected}
                        onClick={() => setSelectedSlot(slot)}
                      >
                        {timeLabel}
                        {!slot.available && <span className="small muted" style={{ display: "block", fontSize: "0.8rem" }}>Taken</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* 6. Reason for Visit */}
          {selectedSlot && (
            <div className="field">
              <label htmlFor="reason-input">
                Reason for visit <span className="small muted">(optional, max 300 characters)</span>
              </label>
              <textarea
                id="reason-input"
                rows={3}
                maxLength={300}
                placeholder="e.g. Routine checkup, sore throat, or follow-up consultation"
                value={reasonForVisit}
                onChange={(e) => setReasonForVisit(e.target.value)}
              />
            </div>
          )}

          {/* 7. Disclaimer & Submit */}
          {selectedSlot && (
            <div className="stack" style={{ marginTop: "1rem" }}>
              <div
                className="notice"
                style={{ background: "#eef4fb", borderLeft: "4px solid var(--blue)", padding: "0.75rem" }}
              >
                <p style={{ margin: 0, fontSize: "0.9rem" }}>
                  <strong>Note:</strong> Submitting this request sends it to the hospital reception for review. It
                  will be listed as pending until approved. You will receive an in-app notification upon confirmation.
                </p>
              </div>

              <button
                type="submit"
                className="primary big"
                disabled={busy || !selectedSlot}
              >
                {busy ? "Submitting request…" : "Submit Appointment Request"}
              </button>
            </div>
          )}
        </form>
      </section>
    </div>
  );
}
