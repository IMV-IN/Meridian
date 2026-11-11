// Sample data for the recreated console screens. All of it is labelled as sample wherever it is shown:
// workspace "Physics Dept. (sample)", addresses at example.ac.in. Nothing here is a real person or institute.
import { feeFor } from "@/lib/format"

import type { PublicOffer } from "./offers"

const GB = 1024 ** 3
const offer = (title: string, model: string, count: number, vramGb: number, price: number, region: string, available = true): PublicOffer => ({
  title,
  region,
  gpu_model: model,
  gpu_count: count,
  vram_bytes_per_gpu: vramGb * GB,
  price_paise_per_gpu_hour: price,
  hourly_total_paise: price * count + feeFor(price * count),
  available,
})

export const SAMPLE_OFFERS: PublicOffer[] = [
  offer("4× A100 80 GB, Mumbai", "A100-SXM4-80GB", 4, 80, 18500, "Mumbai"),
  offer("2× L40S 48 GB, Pune", "L40S", 2, 48, 9200, "Pune"),
  offer("2× RTX A6000, Bengaluru", "RTX A6000", 2, 48, 6000, "Bengaluru"),
  offer("8× H100 80 GB, Hyderabad", "H100-SXM5-80GB", 8, 80, 26000, "Hyderabad", false),
]

export type JobStatus = "queued" | "scheduled" | "running" | "succeeded" | "failed"
export interface SampleJob {
  name: string
  id: string
  kind: "Job" | "Service"
  gpus: number
  by: string
  status: JobStatus
  t: number
  ago: string
}

export const SAMPLE_JOBS: SampleJob[] = [
  { name: "notebook-anita", id: "job_5d02c91e7ab34f6d1e90", kind: "Service", gpus: 1, by: "anita@example.ac.in", status: "running", t: 6120, ago: "1 hour ago" },
  { name: "ddp-check", id: "job_8c1e44a07b9d3f2a5c61", kind: "Job", gpus: 2, by: "rahul@example.ac.in", status: "queued", t: 0, ago: "just now" },
  { name: "qwen-lab-server", id: "job_e7b3910c2d4a8f6b0e12", kind: "Service", gpus: 1, by: "key:nlp-lab", status: "running", t: 50340, ago: "14 hours ago" },
  { name: "resnet-ddp", id: "job_3b90e1c4a7f25d6e8c01", kind: "Job", gpus: 8, by: "meera@example.ac.in", status: "succeeded", t: 4870, ago: "3 hours ago" },
  { name: "hello-gpu", id: "job_a4f644d8b66925f93e8a", kind: "Job", gpus: 1, by: "student01@example.ac.in", status: "failed", t: 12, ago: "yesterday" },
]

/** The demo job that cycles through the real statuses. */
export const DEMO_JOB = 1
const LIFECYCLE: JobStatus[] = ["queued", "scheduled", "running", "succeeded"]

/** One step of the hero demo: running jobs tick up, and every second step the demo job moves on (then loops). */
export function advance(jobs: SampleJob[], tick: number): SampleJob[] {
  return jobs.map((j, k) => {
    let next = j.status === "running" ? { ...j, t: j.t + 3 } : j
    if (k === DEMO_JOB && tick % 2 === 0) {
      const i = LIFECYCLE.indexOf(j.status)
      next = i < LIFECYCLE.length - 1 ? { ...next, status: LIFECYCLE[i + 1] } : { ...next, status: "queued", t: 0 }
      if (next.status === "running") next.ago = "just now"
    }
    return next
  })
}

/** Under reduced motion the demo job sits at Running, 42s. */
export const stillJobs = (): SampleJob[] => SAMPLE_JOBS.map((j, k) => (k === DEMO_JOB ? { ...j, status: "running", t: 42, ago: "just now" } : j))

export interface SampleMonth {
  groups: [group: string, people: number, jobs: number, own: number, rented: number][]
  people: [email: string, group: string, own: number, quota: number][]
}
export const USAGE: Record<string, SampleMonth> = {
  "2026-11": {
    groups: [["vision-lab", 6, 41, 318.25, 0], ["nlp-lab", 4, 27, 206.5, 48], ["cs-601-deep-learning", 58, 212, 431.75, 0]],
    people: [["meera@example.ac.in", "vision-lab", 92.4, 120], ["rahul@example.ac.in", "nlp-lab", 61.08, 80], ["student01@example.ac.in", "cs-601-deep-learning", 9.5, 10], ["student02@example.ac.in", "cs-601-deep-learning", 2.25, 10]],
  },
  "2026-10": {
    groups: [["vision-lab", 6, 66, 512.0, 96], ["nlp-lab", 4, 31, 244.75, 0], ["cs-601-deep-learning", 58, 340, 602.5, 0]],
    people: [["meera@example.ac.in", "vision-lab", 140.75, 120], ["rahul@example.ac.in", "nlp-lab", 70.0, 80], ["student01@example.ac.in", "cs-601-deep-learning", 10.0, 10], ["student02@example.ac.in", "cs-601-deep-learning", 7.5, 10]],
  },
  "2026-09": {
    groups: [["vision-lab", 5, 38, 290.5, 0], ["nlp-lab", 4, 22, 180.25, 0], ["cs-601-deep-learning", 58, 96, 210.0, 0]],
    people: [["meera@example.ac.in", "vision-lab", 80.0, 120], ["rahul@example.ac.in", "nlp-lab", 44.5, 80], ["student01@example.ac.in", "cs-601-deep-learning", 3.75, 10], ["student02@example.ac.in", "cs-601-deep-learning", 4.0, 10]],
  },
}
