// /labs: Meridian for research labs.
import { SitePage } from "./chrome"
import { DDP_RUN, FinalCta, Limits, MarketSection, ReplicaEnv } from "./blocks"
import { NodesMini } from "./console-samples"
import { TEMPLATES } from "./templates"
import { CodeBlock, Figure, Section } from "./ui"
import { COMPUTE_GUIDE } from "./site-lib"
import { UsageReport } from "./usage-report"

const GATEWAY_BACKEND = `[[c:# Add to a Meridian gateway's config.yaml to route to this service]]
[[k:backends]]:
  - [[k:name]]: qwen-lab-server
    [[k:url]]: <endpoint>
    [[k:auth_header]]: [[s:"Bearer \${MERIDIAN_SERVICE_TOKEN}"]]   [[c:# export the token; never write it in the file]]`

export function LabsPage() {
  return (
    <SitePage
      title="Meridian for research labs"
      description="Share a department's GPU servers between research groups, train across several of them, serve a lab's model, and read GPU-hours per group and per person each month."
    >
      <section className="hero hero-page" aria-labelledby="labs-title">
        <div className="wrap">
          <h1 className="display" id="labs-title">
            Your department's GPU servers, shared between labs.
          </h1>
          <p className="lead">
            A group with a deadline can use the GPUs another group is not using, a training run can span several servers, and every month shows how many GPU-hours each group and person used.
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

      <Section
        titleId="pool-title"
        title="Pool the servers, keep the groups apart"
        lead="Enroll each server once. Anyone with a role in the workspace then runs on whichever GPUs are free, without an account on the machine."
      >
        <div className="split rev">
          <dl className="deflist">
            <div>
              <dt>One fleet, one queue</dt>
              <dd>Jobs are placed on free GPUs in the order they arrive. A job that needs more than is free waits in the queue. There are no priorities.</dd>
            </div>
            <div>
              <dt>Groups are labels on people</dt>
              <dd>
                An admin puts each person in a group such as <code>vision-lab</code> under Settings, Members, Limits. The group appears in the usage report.
              </dd>
            </div>
            <div>
              <dt>Hosts stay in their owners' hands</dt>
              <dd>Jobs stay off a host until its owner turns them on, and the owner decides which images may run. A job container gets only the GPUs it was given.</dd>
            </div>
          </dl>
          <Figure caption="The Nodes page of a sample workspace. Hours and names are sample data.">
            <NodesMini />
          </Figure>
        </div>
      </Section>

      <Section
        titleId="train-title"
        title="Train across servers, after a smoke test"
        lead="Check that two servers can reach each other before you spend a night on a real run. The pytorch-ddp template does that in a few minutes."
        alt
      >
        <div className="split rev">
          <CodeBlock head="the smoke test" code={TEMPLATES[1].cmd} />
          <p className="small body-2">
            With two replicas the all-reduce sum is 3. If both print it, rank 0 was reachable. Multi-node jobs need each host's <code>engine_host</code> to be an address the other nodes can reach. The template's script is only a check: replace the last two arguments with your own command.
          </p>
        </div>
        <p className="lead mt-7">Then the same job with your own image, replicas and GPUs. Meridian places all replicas or none, and stops the others if one fails.</p>
        <div className="split rev">
          <CodeBlock head="two servers, four GPUs each" code={DDP_RUN} />
          <ReplicaEnv />
        </div>
      </Section>

      <Section
        titleId="serve-title"
        title="Serve the lab's model"
        lead="Start the vLLM template, or your own model server, as a service. Its job page gives the endpoint, the token and a snippet for the institute gateway."
      >
        <div className="split rev">
          <CodeBlock head="start it" code={TEMPLATES[2].cmd} />
          <CodeBlock head="from the job's page" code={GATEWAY_BACKEND} />
        </div>
        <p className="caption" style={{ marginTop: 12 }}>
          A service is reachable where the host's network allows; Meridian does not tunnel between campuses. The first start downloads the model, so allow a few minutes.
        </p>
      </Section>

      <Section
        titleId="rent-title"
        title="Rent a partner's GPUs when yours are full"
        lead="Partner institutes list idle servers on the marketplace, publicly or only for workspaces they name. You pay the provider's price plus a 2% fee, per second, from prepaid credit; a partner may share a server with you for ₹0."
        alt
      >
        <MarketSection />
      </Section>

      <Section titleId="report-title" title="GPU-hours per group, every month" lead="Read who used how much, per person and per group, without asking IT.">
        <div className="proof-grid">
          <div className="flow-list" style={{ marginTop: 0 }}>
            <div>
              <h3>Own and rented, counted separately</h3>
              <p>Own-node jobs are free but count against quotas. An API-key job counts for the member who created the key, recorded when the job is submitted.</p>
            </div>
            <div>
              <h3>Who sees what</h3>
              <p>Admins see everyone's usage and the groups. Other members see only their own row and the workspace totals.</p>
            </div>
            <div>
              <h3>From the terminal or as a file</h3>
              <p>
                Run <code>meridian compute usage --month 2026-11</code>, or download the month as CSV from Billing.
              </p>
            </div>
          </div>
          <Figure n={1} caption="Billing, GPU-hour usage, as an admin sees it. Sample people and groups.">
            <UsageReport />
          </Figure>
        </div>
      </Section>

      <Limits />
      <FinalCta />
    </SitePage>
  )
}
