interface OnboardingStep {
  id: string;
  label: string;
  step: number;
  completed: boolean;
  completed_at: number | null;
}

export function OnboardingWizard({ onboarding, onAdvance }: {
  onboarding: { steps: OnboardingStep[]; next_step: number | null; all_done: boolean };
  onAdvance: (step: number) => void;
}) {
  const steps = onboarding?.steps ?? [];
  const next = onboarding?.next_step;
  return (
    <div style={{
      border: "1px solid #2a313c", borderRadius: 6, background: "#1f2a36",
      padding: 14, marginBottom: 14, color: "#d6dde6",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 14, color: "#7ee2a0" }}>ONBOARDING</h2>
          <div style={{ color: "#6b7c8f", fontSize: 11 }}>
            {onboarding?.all_done ? "All steps complete." : next ? `Next: step ${next} of 7` : "loading…"}
          </div>
        </div>
        <div style={{ fontSize: 11, color: "#6b7c8f" }}>
          {steps.filter((s: any) => s.completed).length}/{steps.length} done
        </div>
      </div>
      <ol style={{ margin: 0, paddingLeft: 20, fontSize: 12 }}>
        {steps.map((s: any) => (
          <li key={s.step} style={{
            color: s.completed ? "#7ee2a0" : (next === s.step ? "#fbbf24" : "#9fb3c8"),
            padding: "2px 0",
          }}>
            {s.completed ? "✓ " : (next === s.step ? "▶ " : "· ")}
            Step {s.step}. {s.label}
            {s.completed_at ? <span style={{ color: "#6b7c8f", marginLeft: 6 }}>
              ({new Date(s.completed_at * 1000).toLocaleTimeString()})
            </span> : null}
            {!s.completed && next === s.step && (
              <button onClick={() => onAdvance(s.step)}
                style={{
                  marginLeft: 10, background: "#7ee2a0", color: "#0e1116",
                  border: "none", borderRadius: 4, padding: "2px 8px",
                  font: "inherit", fontSize: 10, cursor: "pointer",
                }}>
                mark done
              </button>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}