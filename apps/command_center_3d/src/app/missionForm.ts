/**
 * missionForm — pure validation + serialisation for Mission Studio.
 *
 * Kept out of the component so the rules are unit-testable and so there is one
 * definition of "a submittable mission". The component renders whatever this
 * returns; it does not decide validity itself.
 *
 * Two behaviours worth calling out:
 *  - `permissions` is parsed from a comma/newline list into a de-duplicated,
 *    sorted array so the same grant typed twice is stored once.
 *  - A blank `budgetUnits` is `0` (unbudgeted), but a NON-numeric value is an
 *    error rather than a silent 0 — a typo must not become a free mission.
 */

export interface MissionFormValues {
  title: string;
  projectId: string;
  agent: string;
  goal: string;
  workingDirectory: string;
  context: string;
  permissionsText: string;
  budgetUnits: string;
  connectionRef: string;
}

export const EMPTY_MISSION_FORM: MissionFormValues = {
  title: "",
  projectId: "",
  agent: "workbuddy",
  goal: "",
  workingDirectory: "",
  context: "",
  permissionsText: "",
  budgetUnits: "",
  connectionRef: "",
};

export type MissionFormErrors = Partial<Record<keyof MissionFormValues, string>>;

/**
 * The permission vocabulary the product exposes.
 *
 * These are *requested* scopes. The server and the policy layer decide what is
 * actually granted; requesting one here never grants it.
 */
export const PERMISSION_CATALOG: readonly string[] = [
  "fs.read",
  "fs.write",
  "shell.exec",
  "net.outbound",
  "git.commit",
  "test.run",
  "build.run",
];

export const AGENT_CHOICES: readonly string[] = [
  "workbuddy",
  "claude",
  "codex",
  "hermes",
];

/** Agents the product can launch today (Wave 3 gates the rest). */
export const LAUNCHABLE_AGENTS: readonly string[] = ["workbuddy"];

export function parsePermissions(text: string): string[] {
  return [
    ...new Set(
      text
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ].sort();
}

export function formatPermissions(list: readonly string[]): string {
  return [...list].sort().join(", ");
}

export interface MissionPayload {
  project_id: string;
  title: string;
  goal: string;
  agent: string;
  working_directory: string;
  context: string;
  permissions: string[];
  budget_units: number;
  connection_ref: string | null;
}

export function validateMissionForm(
  v: MissionFormValues,
): { errors: MissionFormErrors; payload: MissionPayload | null } {
  const errors: MissionFormErrors = {};

  const title = v.title.trim();
  if (!title) errors.title = "A mission title is required.";

  const projectId = v.projectId.trim();
  if (!projectId) errors.projectId = "Choose the project this mission runs under.";

  const agent = v.agent.trim();
  if (!agent) errors.agent = "Choose an agent.";

  let budget = 0;
  const rawBudget = v.budgetUnits.trim();
  if (rawBudget !== "") {
    const n = Number(rawBudget);
    if (!Number.isFinite(n) || n < 0) {
      errors.budgetUnits = "Budget must be a number ≥ 0 (leave blank for unbudgeted).";
    } else {
      budget = n;
    }
  }

  if (Object.keys(errors).length > 0) return { errors, payload: null };

  const connectionRef = v.connectionRef.trim();
  return {
    errors,
    payload: {
      project_id: projectId,
      title,
      goal: v.goal.trim(),
      agent,
      working_directory: v.workingDirectory.trim(),
      context: v.context.trim(),
      permissions: parsePermissions(v.permissionsText),
      budget_units: budget,
      connection_ref: connectionRef ? connectionRef : null,
    },
  };
}

/**
 * The launch sequence a mission must walk.
 *
 * The server's state machine refuses DRAFT -> RUNNING, and it is right to: a
 * mission that claims to be running without passing through the launch
 * handshake would be a lie the UI invented. This constant is the single
 * declaration of that handshake, and `launch_sequence_ok` is the falsifier.
 */
export const LAUNCH_SEQUENCE: readonly string[] = ["READY", "STARTING", "RUNNING"];

export function launchSequenceOk(applied: readonly string[]): boolean {
  return (
    applied.length === LAUNCH_SEQUENCE.length &&
    applied.every((s, i) => s === LAUNCH_SEQUENCE[i])
  );
}
