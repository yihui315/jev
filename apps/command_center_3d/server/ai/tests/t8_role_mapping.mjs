// T8 - six-role mapping is documented, complete, and explicitly a draft.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ROLE_MAPPING, ROLE_MAPPING_STATUS, CODE_ROLES, roleMappingDocument } from "../roles-mapping.mjs";
import { AI_DIR } from "./lib/fixtures.mjs";
import { assert, assertEqual } from "./lib/harness.mjs";

export const id = "t8_role_mapping";
export const title = "Six-role mapping: 6 rows, no code rename, OWNER-DECIDED draft";

const DOC_PATH = join(AI_DIR, "..", "..", "..", "..", "docs", "JEV_AI_DECISION_SCHEMA.md");

export async function run(suite) {
  await suite.check("mapping covers all six V1.6 requirement roles once", () => {
    assertEqual(ROLE_MAPPING.length, 6);
    const names = ROLE_MAPPING.map((r) => r.requirementRole);
    assertEqual(new Set(names).size, 6);
    assertEqual(ROLE_MAPPING.map((r) => r.order).join(","), "1,2,3,4,5,6");
    return names.join(" | ");
  });

  await suite.check("every mapped code role exists in the frozen orchestrator vocabulary", () => {
    for (const row of ROLE_MAPPING) {
      if (row.codeRole !== null) assert(CODE_ROLES.includes(row.codeRole), "unknown code role: " + row.codeRole);
    }
    const used = ROLE_MAPPING.map((r) => r.codeRole).filter(Boolean);
    assertEqual(new Set(used).size, used.length, "two requirement roles map to the same code role");
    assertEqual(ROLE_MAPPING.filter((r) => r.codeRole === null).length, 1, "exactly one role must be unmapped in code (Supervisor)");
    return used.join(",");
  });

  await suite.check("mapping is marked as an owner-decided draft and renames nothing", () => {
    assert(/OWNER-DECIDED/.test(ROLE_MAPPING_STATUS), ROLE_MAPPING_STATUS);
    assert(/DRAFT/i.test(ROLE_MAPPING_STATUS), ROLE_MAPPING_STATUS);
    assert(ROLE_MAPPING.every((r) => r.codeChanged === false), "no row may claim a code rename");
    const doc = roleMappingDocument();
    assertEqual(doc.renamePerformed, false);
    return ROLE_MAPPING_STATUS;
  });

  await suite.check("documentation carries the same draft mapping", () => {
    const text = readFileSync(DOC_PATH, "utf8");
    assert(/OWNER-DECIDED/.test(text), "docs must state OWNER-DECIDED");
    for (const row of ROLE_MAPPING) {
      assert(text.includes(row.requirementRole), "doc missing requirement role: " + row.requirementRole);
      if (row.codeRole) assert(text.includes(row.codeRole), "doc missing code role: " + row.codeRole);
    }
    return DOC_PATH.replace(/\\/g, "/");
  });
}
