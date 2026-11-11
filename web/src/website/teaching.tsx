// /teaching: Meridian for teaching.
import { SitePage } from "./chrome"
import { FinalCta, Limits } from "./blocks"
import { ComputeLimitsDialog, DefaultQuotaSample, ServicePanelSample } from "./console-samples"
import { TEMPLATES } from "./templates"
import { CodeBlock, Figure, Section } from "./ui"
import { COMPUTE_GUIDE } from "./site-lib"
import { UsageReport } from "./usage-report"

const QUOTA_REFUSED = `[[p:$]] meridian compute run --template jupyterlab --name notebook
[[ok:error: monthly GPU-hour quota reached: 10.00 of 10 GPU-hours used in 2026-11; it resets on 2026-12-01 (UTC) (HTTP 409)]]`

export function TeachingPage() {
  return (
    <SitePage
      title="Meridian for teaching"
      description="Give every student in a course a JupyterLab notebook on a campus GPU, with a monthly GPU-hour quota the instructor sets once."
    >
      <section className="hero hero-page" aria-labelledby="teach-title">
        <div className="wrap">
          <h1 className="display" id="teach-title">
            A GPU notebook for every student in the course.
          </h1>
          <p className="lead">
            Students start JupyterLab from a template on the campus GPUs. The instructor sets a monthly GPU-hour quota once, and a job over quota is refused with the date it resets.
          </p>
          <div className="hero-actions">
            <a className="btn btn-lamp" href={`${COMPUTE_GUIDE}#set-up-a-node-to-run-jobs`}>
              Enroll your first server
            </a>
            <a className="btn btn-ghost" href="/ui/">
              Sign in to the console
            </a>
          </div>
        </div>
      </section>

      <Section titleId="setup-title" title="Set up the course once" lead="Three steps in the console, before the first lab session.">
        <div className="steps">
          <div className="step">
            <div>
              <h3 className="h3">Invite the students</h3>
              <p>Invite them by email under Settings, Members. If the operator of your Meridian Cloud has limited sign-in to your institute's email domain, which is set once for the whole deployment and not per workspace, they sign in with their institute account.</p>
            </div>
            <div className="deflist-note">
              <p className="small body-2">Students need a role in the workspace to run jobs. The operator role can run and cancel jobs; viewers can only look.</p>
            </div>
          </div>
          <div className="step">
            <div>
              <h3 className="h3">Name the course as a group</h3>
              <p>Under Members, Limits, put each student in a group named after the course, such as <code>cs-601-deep-learning</code>. The group appears in the usage report.</p>
            </div>
            <ComputeLimitsDialog />
          </div>
          <div className="step">
            <div>
              <h3 className="h3">Set the quota</h3>
              <p>Give everyone a default monthly quota in GPU-hours, or set one per student. It caps use on your own nodes and resets on the first of each UTC month. Leave it empty for no limit.</p>
            </div>
            <DefaultQuotaSample />
          </div>
        </div>
      </Section>

      <Section
        titleId="student-title"
        title="What a student does"
        lead="Start the JupyterLab template, open the job's page, and use the notebook link."
        alt
      >
        <div className="split rev">
          <CodeBlock head="from the console or the CLI" code={TEMPLATES[0].cmd} />
          <ServicePanelSample />
        </div>
        <p className="caption" style={{ marginTop: 12 }}>
          Notebooks stop after 8 hours by default, so a forgotten one does not hold a GPU. Files live inside the container and are lost when the job ends: tell students to download their notebooks or save them to their own storage.
        </p>
      </Section>

      <Section titleId="instructor-title" title="What the instructor sees" lead="Who has used how much this month, and who is close to the quota.">
        <div className="proof-grid">
          <div className="flow-list" style={{ marginTop: 0 }}>
            <div>
              <h3>One row per student</h3>
              <p>Own GPU-hours, the quota and what is left. Download the month as CSV, or run <code>meridian compute usage</code>.</p>
            </div>
            <div>
              <h3>Admins see everyone</h3>
              <p>Admins see everyone's usage and the groups. A student sees only their own row and the workspace totals.</p>
            </div>
          </div>
          <Figure n={1} caption="Billing, GPU-hour usage, as an admin sees it. Sample students.">
            <UsageReport />
          </Figure>
        </div>
      </Section>

      <Section
        titleId="quota-title"
        title="What happens at the quota"
        lead="The quota stops the next job, not the one that is running."
        alt
      >
        <div className="split rev">
          <CodeBlock head="a student over quota" code={QUOTA_REFUSED} />
          <dl className="deflist">
            <div>
              <dt>A new job is refused</dt>
              <dd>It gets a 409 that says what was used and when the quota resets, the first of next month in UTC.</dd>
            </div>
            <div>
              <dt>A queued job waits</dt>
              <dd>A job already queued when its owner went over stays queued, with "waiting: monthly GPU-hour quota used", and starts when the quota is raised or the month rolls over.</dd>
            </div>
            <div>
              <dt>A running job continues</dt>
              <dd>Quotas count hours already used, so a student just under the line can start a job that takes them over it. An admin who needs a hard stop cancels the job.</dd>
            </div>
          </dl>
        </div>
      </Section>

      <Limits
        only={["storage", "restarts", "secrets", "fcfs", "containers", "replica"]}
        lead="Read this before you plan a semester around it. The first one matters most for a course: there is no storage service, so notebooks are lost when the job ends."
      />
      <FinalCta />
    </SitePage>
  )
}
