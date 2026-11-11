// The terminal session of Figure 2. The output format is meridian/cli/compute.py's own (`_table`, `_follow`,
// `_print_job`); the names and IDs are samples.
import { TEMPLATES } from "./templates"

/** meridian/cli/compute.py `_table`: columns padded to their widest cell, two spaces apart, trailing space trimmed. */
export function table(rows: string[][]): string {
  const widths = rows[0].map((_, i) => Math.max(...rows.map((r) => r[i].length)))
  return rows.map((r) => r.map((c, i) => c.padEnd(widths[i])).join("  ").trimEnd()).join("\n")
}

const usage = table([
  ["PERSON", "GROUP", "OWN GPU-H", "RENTED GPU-H", "JOBS", "QUOTA", "LEFT"],
  ["meera@example.ac.in", "vision-lab", "92.40", "0.00", "14", "120", "27.60"],
  ["rahul@example.ac.in", "nlp-lab", "61.08", "48.00", "9", "80", "18.92"],
  ["student01@example.ac.in", "cs-601-deep-learning", "9.50", "0.00", "6", "10", "0.50"],
  ["TOTAL", "", "162.98", "48.00", "29", "", ""],
])

const templates =
  table([["ID", "TYPE", "GPUS", "WHAT IT DOES"], ...TEMPLATES.map((t) => [t.id, t.kind, `${t.replicas} x ${t.gpus_per_replica}`, t.summary])]) +
  "\nRun one with: meridian compute run --template <id> [--follow]"

const P = "[[p:$]] "
const JOB = "job_8c1e44a07b9d3f2a5c61"

/** [text, ms to wait before the next line] */
export const SESSION: [string, number][] = [
  [`${P}meridian compute templates`, 500],
  [templates, 900],
  ["", 200],
  [`${P}meridian compute run --template pytorch-ddp --name ddp-check --follow`, 600],
  [`[[d:submitted ${JOB} (queued)]]`, 500],
  ["[[d:queued: not placed]]", 900],
  ["[[d:scheduled: Pending, Pending]]", 900],
  ["[[d:scheduled: Starting, Starting]]", 1100],
  ["[[ok:running: Running, Running]]", 1300],
  ["[[ok:succeeded: Succeeded, Succeeded]]", 400],
  [`${JOB}  ddp-check  succeeded`, 200],
  ["[[d:--- replica 0 on gpu-01: Succeeded exit=0]]\nrank 0 of 2: all_reduce sum=3 (expected 3) on NVIDIA A100-SXM4-80GB", 200],
  ["[[d:--- replica 1 on gpu-02: Succeeded exit=0]]\nrank 1 of 2: all_reduce sum=3 (expected 3) on NVIDIA A100-SXM4-80GB", 900],
  ["", 200],
  [`${P}meridian compute usage --month 2026-11`, 600],
  [`usage for 2026-11 (UTC)\n${usage}`, 0],
]
