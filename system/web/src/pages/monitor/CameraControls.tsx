import { useRef, useState, type CSSProperties } from "react";
import { usePolling } from "../../hooks/usePolling";
import { S } from "./styles";
import { HW } from "./types";

type Settings = { fps: number; exposure_compensation: number; exposure_mode: string; metering: string; shutter_us: number; gain: number; autofocus_mode: string; lens_position: number; awb: string; flicker_hz: number; hdr: string };
type Controls = { supported: boolean; settings: Settings; defaults: Settings; requested_fps?: number; measured_fps?: number | null; frame_age_ms?: number | null };
type Field = { key: keyof Settings; label: string; min?: number; max?: number; step?: number; options?: [string, string][] };
const fields: Field[] = [
  { key: "fps", label: "Capture frames per second", min: 1, max: 40, step: 1 },
  { key: "exposure_compensation", label: "Brightness adjustment (EV)", min: -2, max: 2, step: 0.1 },
  { key: "exposure_mode", label: "Exposure preference", options: [["normal", "Normal"], ["sport", "Short exposure for movement"]] },
  { key: "metering", label: "Measure brightness from", options: [["centre", "Center of image"], ["spot", "Small center spot"], ["average", "Whole image"]] },
  { key: "shutter_us", label: "Exposure time (µs; 0 = Auto)", min: 0, max: 100000, step: 1 },
  { key: "gain", label: "Sensor gain (0 = Auto)", min: 0, max: 16, step: 0.1 },
  { key: "autofocus_mode", label: "Focus", options: [["continuous", "Continuous autofocus"], ["auto", "Autofocus on start"], ["manual", "Manual"]] },
  { key: "lens_position", label: "Manual focus (diopters; 0 = infinity)", min: 0, max: 32, step: 0.1 },
  { key: "awb", label: "White balance", options: [["auto", "Auto"], ["indoor", "Indoor"], ["daylight", "Daylight"]] },
  { key: "flicker_hz", label: "Lighting flicker reduction", options: [["0", "Off"], ["50", "50 Hz"], ["60", "60 Hz"]] },
  { key: "hdr", label: "High dynamic range (HDR)", options: [["off", "Off"], ["auto", "Auto"]] },
];
const inputStyle: CSSProperties = { width: "100%", padding: "7px 9px", borderRadius: 6, background: "var(--lm-surface)", color: "var(--lm-text)", border: "1px solid var(--lm-border)", fontSize: 12 };
const buttonStyle: CSSProperties = { ...inputStyle, width: "auto", cursor: "pointer" };
async function readControls(response: Response): Promise<Controls> {
  const result = await response.json();
  if (!response.ok) {
    const detail = typeof result.detail === "string" ? result.detail : result.message;
    throw new Error(typeof detail === "string" ? detail : `Camera settings request failed (${response.status}).`);
  }
  return result;
}
export function CameraControls({ onApplied }: { onApplied: () => void }) {
  const [controls, setControls] = useState<Controls | null>(null);
  // Separate draft values preserve incomplete numeric inputs across polling.
  const [draft, setDraft] = useState<Partial<Record<keyof Settings, string>>>({});
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const saving = useRef(false);
  usePolling(async (signal) => {
    if (saving.current) return;
    const version = requestVersion.current;
    try {
      const result = await readControls(await fetch(`${HW}/camera/controls`, { signal }));
      if (version !== requestVersion.current) return;
      setControls(result);
      setLoadError(null);
    } catch (error) {
      if (version === requestVersion.current) setLoadError(error instanceof Error ? error.message : "Could not load camera settings.");
    }
  }, 3000);
  const apply = async (reset = false) => {
    if (!controls || saving.current) return;
    saving.current = true;
    requestVersion.current += 1;
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const settings: Record<string, string | number | boolean> = reset ? { reset: true } : {};
      if (!reset) for (const field of fields) {
        const value = draft[field.key];
        if (value !== undefined) settings[field.key] = typeof controls.settings[field.key] === "number" ? Number(value) : value;
      }
      const result = await readControls(await fetch(`${HW}/camera/controls`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) }));
      setControls(result);
      setDraft({});
      setLoadError(null);
      setNotice(reset ? "Automatic camera defaults restored." : "Camera settings applied.");
      onApplied();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not apply camera settings.");
    } finally {
      saving.current = false;
      setBusy(false);
    }
  };
  if (controls?.supported === false) return null;
  const dirty = Object.keys(draft).length > 0;
  return (
    <section style={S.card} aria-label="Camera settings">
      <div style={S.cardLabel}>Camera Settings</div>
      {loadError && <p role="alert" style={{ color: "var(--lm-red)", fontSize: 12 }}>{loadError}</p>}
      {!controls ? <p style={{ fontSize: 12 }}>Loading camera settings…</p> : (
        <form onSubmit={(event) => { event.preventDefault(); void apply(); }}>
          <p style={{ color: "var(--lm-text-muted)", fontSize: 12, lineHeight: 1.6 }}>
            Apply briefly interrupts the live preview. Longer exposures brighten the image but can blur moving faces.
            Use center metering and a small brightness adjustment for a backlit face. Aperture is fixed on this camera.
          </p>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, marginBottom: 14, color: "var(--lm-text-dim)" }}>
            <span>Requested capture: {controls.requested_fps ?? controls.settings.fps} fps</span>
            <span>Measured capture: {typeof controls.measured_fps === "number" ? `${controls.measured_fps.toFixed(1)} fps` : "Waiting for frames"}</span>
            <span>Latest frame age: {typeof controls.frame_age_ms === "number" ? `${Math.round(controls.frame_age_ms)} ms` : "Unavailable"}</span>
          </div>
          <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
              {fields.map((field) => {
                const value = draft[field.key] ?? String(controls.settings[field.key]);
                const disabled = field.key === "lens_position" && (draft.autofocus_mode ?? controls.settings.autofocus_mode) !== "manual";
                const change = (value: string) => { setDraft((current) => ({ ...current, [field.key]: value })); setNotice(null); };
                return (
                  <label key={field.key} style={{ display: "grid", gap: 5, fontSize: 12, color: "var(--lm-text-dim)", opacity: disabled ? 0.5 : 1 }}>
                    {field.label}
                    {field.options ? (
                      <select style={inputStyle} value={value} onChange={(event) => change(event.target.value)}>
                        {field.options.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    ) : (
                      <input style={inputStyle} type="number" required disabled={disabled} min={field.min} max={field.max} step={field.step} value={value} onChange={(event) => change(event.target.value)} />
                    )}
                  </label>
                );
              })}
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginTop: 16 }}>
              <button type="submit" disabled={!dirty} style={{ ...buttonStyle, opacity: dirty ? 1 : 0.5 }}>Apply</button>
              <button type="button" style={buttonStyle} onClick={() => void apply(true)}>Reset to Auto</button>
              <span style={{ fontSize: 12, color: "var(--lm-text-muted)" }}>{busy ? "Applying camera settings…" : dirty ? "Unsaved changes" : ""}</span>
            </div>
          </fieldset>
          {actionError && <p role="alert" style={{ fontSize: 12, color: "var(--lm-red)" }}>{actionError}</p>}
          {notice && <p role="status" style={{ fontSize: 12, color: "var(--lm-green)" }}>{notice}</p>}
        </form>
      )}
    </section>
  );
}
