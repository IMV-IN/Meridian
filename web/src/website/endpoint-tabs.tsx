// Block 2: what a request, a gateway config and PII masking look like.
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import { CodeBlock } from "./ui"

const CURL = `[[p:$]] curl -i https://llm.example.ac.in/v1/chat/completions \\
  -H [[s:"Authorization: Bearer mrdn_…"]] \\
  -H [[s:"Content-Type: application/json"]] \\
  -d [[s:'{"model": "qwen2.5-7b-instruct",
       "messages": [{"role": "user",
                     "content": "Summarise this abstract"}]}']]

[[d:HTTP/1.1 200 OK]]
[[k:x-meridian-backend]]: vllm-gpu-01
[[k:x-request-id]]: 7f3c…`

const CONFIG = `[[k:auth]]:
  [[k:enabled]]: true
  [[k:keys_file]]: /secrets/keys.yaml   [[c:# org_id, team_id, user_id, allowed_models]]
[[k:budgets]]:
  [[k:enabled]]: true
  [[k:orgs]]:
    [[s:physics]]:
      [[k:monthly]]: { tokens: 100000000 }
  [[k:teams]]:
    [[s:physics/condensed-matter]]:
      [[k:daily]]: { tokens: 2000000 }
  [[k:users]]:
    [[s:physics/student-17]]:
      [[k:daily]]: { tokens: 200000 }
[[k:pii]]:
  [[k:enabled]]: true
  [[k:policy]]: redact_and_replace
  [[k:entities]]: []   [[c:# every India pack type]]`

export function EndpointTabs() {
  return (
    <Tabs defaultValue="request" className="etabs">
      <TabsList className="tablist" aria-label="Endpoint examples">
        <TabsTrigger value="request">Request</TabsTrigger>
        <TabsTrigger value="config">Keys and budgets</TabsTrigger>
        <TabsTrigger value="pii">PII masking</TabsTrigger>
      </TabsList>
      <TabsContent value="request" className="tabpanel">
        <CodeBlock head="any OpenAI client works" code={CURL} />
        <p className="caption" style={{ marginTop: 12 }}>
          The gateway also serves <code>/v1/models</code> and streaming responses. Change the base URL in an OpenAI SDK and nothing else.
        </p>
      </TabsContent>
      <TabsContent value="config" className="tabpanel">
        <CodeBlock head="gateway config.yaml" code={CONFIG} />
        <div className="tree" role="group" aria-label="How budgets nest">
          <ul>
            <li>
              <b>physics</b>
              <em>100M tokens a month</em>
              <ul>
                <li>
                  <b>condensed-matter</b>
                  <em>2M tokens a day</em>
                  <ul>
                    <li>
                      <b>student-17</b>
                      <em>200k tokens a day</em>
                    </li>
                  </ul>
                </li>
              </ul>
            </li>
          </ul>
        </div>
      </TabsContent>
      <TabsContent value="pii" className="tabpanel">
        <div className="pii">
          <div className="row">
            <span>Sent by the app</span>
            <code>
              Draft a reply to the student. PAN <mark>ABCDE1234F</mark>, phone <mark>9876543210</mark>.
            </code>
          </div>
          <div className="row">
            <span>Seen by the model</span>
            <code>
              Draft a reply to the student. PAN <mark className="safe">ABCDE****F</mark>, phone <mark className="safe">XXXXXX3210</mark>.
            </code>
          </div>
        </div>
        <p className="caption" style={{ marginTop: 14 }}>
          With <code>policy: redact_and_replace</code>. Matched values are never written to the logs. Set <code>block</code> to refuse the request instead, or set a different policy per key.
        </p>
      </TabsContent>
    </Tabs>
  )
}
