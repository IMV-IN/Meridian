// The landing page (route /). The spec is in the PR; every claim sits next to a recreated console screen,
// a real CLI command or a real config file, and sample data is labelled as sample.
import { SitePage } from "./chrome"
import { DDP_RUN, FinalCta, Limits, MarketSection, PageLink, ReplicaEnv } from "./blocks"
import { CampusDiagram } from "./campus-diagram"
import { ComputeLimitsDialog, JobPageSample, NodesMini } from "./console-samples"
import { EndpointTabs } from "./endpoint-tabs"
import { HeroConsole } from "./hero-console"
import { RailNav } from "./rail-nav"
import { ShareMode } from "./share-mode"
import { TemplatePicker } from "./template-picker"
import { TerminalReplay } from "./terminal-replay"
import { CodeBlock, CopyChip, Figure } from "./ui"
import { COMPUTE_GUIDE, installCommand } from "./site-lib"
import { UsageReport } from "./usage-report"

const TITLE = "Meridian: a GPU cloud for your campus"
const DESCRIPTION =
  "Meridian turns the GPU servers your institute already owns into one cloud for notebooks, training jobs and model servers, with GPU-hour quotas, usage per lab and an OpenAI-compatible endpoint."

const RAIL: [string, string][] = [
  ["gpu-cloud", "Campus GPU cloud"],
  ["llm", "Campus LLM endpoint"],
  ["training", "Training across servers"],
  ["share", "Share with partners"],
]

const ENROLL = `[[c:# /etc/meridian-node/config.yaml]]
[[k:control_plane_url]]: https://<your-meridian-cloud>:8443
[[k:mode]]: managed-host
[[k:policy]]:
  [[k:management_enabled]]: true
  [[k:jobs_enabled]]: true
  [[k:allowed_job_image_digests]]:
    - [[s:"sha256:<64 hex>"]]
  [[k:engine_host]]: 10.0.0.5   [[c:# reachable by the other nodes]]

[[p:$]] export MERIDIAN_NODE_TOKEN='<join token>'
[[p:$]] meridian-node run`

export function LandingPage() {
  return (
    <SitePage title={TITLE} description={DESCRIPTION}>
      <section className="hero" id="top" aria-labelledby="hero-title">
        <div className="wrap">
          <h1 className="display" id="hero-title">
            A GPU cloud for your campus, on the GPUs you already own.
          </h1>
          <p className="lead">
            Meridian joins the GPU servers in your labs and data centre into one place to run notebooks, training jobs and model servers. Every lab sees the GPU-hours it used, and idle GPUs can go to a partner institute.
          </p>
          <div className="hero-actions">
            <a className="btn btn-lamp" href={`${COMPUTE_GUIDE}#set-up-a-node-to-run-jobs`}>
              Enroll your first server
            </a>
            <a className="btn btn-ghost" href="/ui/">
              Sign in to the console
            </a>
            <span className="cli-line">
              <span>Install the gateway and CLI</span>
              <CopyChip text={installCommand(window.location.origin)} label="Copy install command" />
            </span>
          </div>

          <Figure
            n={1}
            caption="The console of a sample workspace, recreated from the product. Jobs move through queued, scheduled, running and succeeded as they would on real nodes; select a page in the console's sidebar to look around. Names and numbers are sample data."
          >
            <HeroConsole />
          </Figure>

          <div className="routes">
            <div>
              <h2>Research labs</h2>
              <p>Share a department's servers between groups, train across several of them, and see GPU-hours per lab each month.</p>
              <PageLink to="/labs">How labs use it</PageLink>
            </div>
            <div>
              <h2>Courses</h2>
              <p>Give every student a JupyterLab notebook on a campus GPU and a monthly GPU-hour quota, set once by the instructor.</p>
              <PageLink to="/teaching">How courses use it</PageLink>
            </div>
            <div>
              <h2>The IT and HPC team</h2>
              <p>Enroll servers with one agent, keep datasets and prompts on campus, and decide which images may run on each host.</p>
              <a className="link" href="#on-campus">
                What stays on campus
              </a>
            </div>
          </div>
        </div>
      </section>

      <section className="product" id="product" aria-label="Product">
        <div className="wrap product-grid">
          <RailNav items={RAIL} />
          <div>
            <article className="pblock" id="gpu-cloud" aria-labelledby="b1">
              <h2 className="h2" id="b1">
                Your servers, as a cloud people can use
              </h2>
              <p className="lead">
                Install the node agent on each GPU server and they show up in one console. Anyone with a role in the workspace can then run a notebook or a job on a free GPU, without an account on the machine.
              </p>
              <div className="steps">
                <div className="step">
                  <div>
                    <h3 className="h3">Enroll each server</h3>
                    <p>Mint a one-time join token in the console under Enrollment, then start the agent on the host. Jobs stay off until the host's owner turns them on, and the owner decides which images may run.</p>
                  </div>
                  <CodeBlock head="on the GPU host" code={ENROLL} />
                </div>
                <div className="step">
                  <div>
                    <h3 className="h3">See the fleet</h3>
                    <p>Every node reports its GPUs, free memory and what is running. A job container gets only the GPUs it was given, with no host mounts and every capability dropped.</p>
                  </div>
                  <NodesMini />
                </div>
                <div className="step step-wide">
                  <div>
                    <h3 className="h3">Run something</h3>
                    <p>Start from a template, or bring any image pinned by digest. Without a free GPU, the job waits in the queue. Pick a template to see what it runs.</p>
                  </div>
                  <TemplatePicker />
                </div>
              </div>
            </article>

            <article className="pblock" id="llm" aria-labelledby="b2">
              <h2 className="h2" id="b2">
                One OpenAI-compatible endpoint for the whole institute
              </h2>
              <p className="lead">
                Run the Meridian gateway in front of the model servers you host, such as vLLM, SGLang or Ollama. Apps, notebooks and theses point at one URL. Each key belongs to a department, a team and a person, with its own limits.
              </p>
              <div className="split">
                <dl className="deflist">
                  <div>
                    <dt>A key per person or project</dt>
                    <dd>Every request carries a key. The key decides which models it may call, and every request is logged against it without the prompt.</dd>
                  </div>
                  <div>
                    <dt>Token budgets, department down to person</dt>
                    <dd>Daily and monthly token caps for each department, team, person or key. A request over its cap is refused until the period resets.</dd>
                  </div>
                  <div>
                    <dt>India PII pack</dt>
                    <dd>Finds Aadhaar, PAN, GSTIN, IFSC, UPI IDs and Indian phone numbers in requests. Block the request, mask the values before the model sees them, mask them only in logs, or just count them.</dd>
                  </div>
                  <div>
                    <dt>Sign in with the institute's account</dt>
                    <dd>
                      People sign in to the console with Google or an emailed code. The operator of a Meridian Cloud can limit sign-in to an email domain with <code>MERIDIAN_CONTROL_ALLOWED_EMAIL_DOMAINS</code>, once for the whole deployment, not per workspace; everyone else needs an invitation.
                    </dd>
                  </div>
                </dl>
                <EndpointTabs />
              </div>
            </article>

            <article className="pblock" id="training" aria-labelledby="b3">
              <h2 className="h2" id="b3">
                Train on several servers as one job
              </h2>
              <p className="lead">
                Ask for replicas and GPUs per replica. Meridian places all of them or none, tells each replica its rank and where rank 0 is, and stops the others if one fails. A job is launched at most once, even if a node restarts mid-launch.
              </p>
              <div className="split rev">
                <CodeBlock head="two servers, four GPUs each" code={DDP_RUN} />
                <ReplicaEnv />
              </div>
            </article>

            <article className="pblock" id="share" aria-labelledby="b4">
              <h2 className="h2" id="b4">
                Lend idle GPUs to a partner institute, or list them for anyone
              </h2>
              <p className="lead">
                A listing offers one server's idle GPUs. While it is listed, your own new jobs stay off those GPUs, and you can pause it whenever you need them back. You never see what a renter runs: only that a GPU is taken.
              </p>
              <ShareMode />
              <MarketSection />
            </article>
          </div>
        </div>
      </section>

      <section className="section" id="proof" aria-labelledby="proof-title">
        <div className="wrap">
          <h2 className="h2" id="proof-title">
            From a laptop on the campus network
          </h2>
          <p className="lead mt-4">List the templates, check that two servers can train together, then read who used how much this month. Three commands.</p>
          <div className="proof-term">
            <Figure n={2} caption="A CLI session, with sample IDs and names. The output format is the CLI's own.">
              <TerminalReplay />
            </Figure>
          </div>
          <div className="proof-grid">
            <div>
              <h3 className="h2 h2-sm">GPU-hours per lab, every month</h3>
              <div className="flow-list">
                <div>
                  <h3>Per person and per group</h3>
                  <p>Own-node hours and rented hours are counted separately. An API-key job counts for the member who created the key, recorded when the job is submitted.</p>
                </div>
                <div>
                  <h3>Quotas that stop the next job</h3>
                  <p>When someone reaches their monthly quota, their next job on your own nodes is refused or waits for the first of the month. A running job is never stopped.</p>
                </div>
                <div>
                  <h3>Admins see everyone</h3>
                  <p>Admins see everyone's usage and the groups. Other members see only their own row and the workspace totals.</p>
                </div>
              </div>
            </div>
            <Figure
              n={3}
             
              caption={
                <>
                  Billing, GPU-hour usage, as an admin sees it, with sample people and groups. Download it as CSV, or run <code>meridian compute usage --month 2026-11</code>.
                </>
              }
            >
              <UsageReport />
            </Figure>
          </div>

          <div className="diagram" id="on-campus">
            <div>
              <h3 className="h2 h2-sm">What stays on campus</h3>
              <div className="flow-list">
                <div>
                  <h3>Stays on your network</h3>
                  <p>Datasets, checkpoints and model weights, inside your jobs and your storage. Prompts and completions, which the gateway serves from your own model servers.</p>
                </div>
                <div>
                  <h3>Goes to Meridian Cloud</h3>
                  <p>Node and gateway health, job specs you submit, GPU-hour counts, and the last 8 KB of each job's output. Gateways send backend state and request counts, never prompts or keys.</p>
                </div>
                <div>
                  <h3>Connections go outward</h3>
                  <p>Nodes and gateways call out to the control plane. A node opens no inbound port except the one a job or model server publishes. If the cloud is unreachable, running engines keep running.</p>
                </div>
              </div>
            </div>
            <Figure n={4} caption="You can also run the control plane yourself, on a machine on campus.">
              <CampusDiagram />
            </Figure>
          </div>
        </div>
      </section>

      <section className="section" aria-labelledby="aud-title" style={{ background: "var(--bg-alt)" }}>
        <div className="wrap">
          <h2 className="h2" id="aud-title">
            For the lab down the corridor, and for the course of 120
          </h2>
          <div className="aud">
            <article id="research" aria-labelledby="research-h">
              <h3 id="research-h">Research labs</h3>
              <ul>
                <li>Pool the department's servers so a group with a deadline can use GPUs another group is not using.</li>
                <li>
                  Put each person in a group, such as <code>vision-lab</code>, and read GPU-hours per group and per person each month.
                </li>
                <li>Serve the lab's fine-tuned model with the vLLM template and reach it through the institute endpoint.</li>
                <li>Rent a partner's A100s for a week when your own are full, paid from prepaid credit.</li>
              </ul>
              <JobPageSample />
              <p style={{ marginTop: 20 }}>
                <PageLink to="/labs">Meridian for research labs</PageLink>
              </p>
            </article>
            <article id="teaching" aria-labelledby="teaching-h">
              <h3 id="teaching-h">Teaching and courses</h3>
              <ul>
                <li>Set a monthly GPU-hour quota once for the course, or per student. A job over quota is refused with the date the quota resets.</li>
                <li>Students start JupyterLab from the template and open it with the token link on the job's page. Notebooks stop after 8 hours by default.</li>
                <li>Running jobs are never stopped mid-way; the quota stops the next one.</li>
              </ul>
              <ComputeLimitsDialog />
              <p style={{ marginTop: 20 }}>
                <PageLink to="/teaching">Meridian for teaching</PageLink>
              </p>
            </article>
          </div>
        </div>
      </section>

      <Limits />
      <FinalCta />
    </SitePage>
  )
}
