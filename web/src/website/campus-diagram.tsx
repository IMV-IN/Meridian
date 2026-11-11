// Figure 4: what is on campus, what goes to Meridian Cloud, and which way the connections point.
export function CampusDiagram() {
  const sans = "IBM Plex Sans Variable, IBM Plex Sans, sans-serif"
  return (
    <svg viewBox="0 0 640 420" role="img" style={{ overflow: "visible" }} aria-labelledby="dg-t dg-d">
      <title id="dg-t">Campus network and Meridian Cloud</title>
      <desc id="dg-d">
        Inside the campus boundary are GPU servers running the node agent, the gateway in front of model servers, and the people using them. Arrows go outward from the nodes and the gateway to Meridian Cloud, carrying health, job specs and usage counts. Data, weights and prompts stay inside.
      </desc>
      <defs>
        <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0 0L10 5L0 10z" fill="#F2B441" />
        </marker>
      </defs>
      <rect x="8" y="40" width="404" height="368" rx="14" fill="#111a2c" stroke="#36425F" strokeDasharray="5 5" />
      <text x="28" y="72" fill="#AEB5C6" fontFamily={sans} fontSize="14">
        Your campus network
      </text>
      <g fontFamily={sans} fontSize="13" fill="#ECE6D8">
        <rect x="28" y="96" width="170" height="124" rx="10" fill="#161F33" stroke="#26314A" />
        <text x="44" y="122" fontWeight="600">
          GPU servers
        </text>
        <text x="44" y="144" fill="#AEB5C6">
          meridian-node agent
        </text>
        <g transform="translate(44 160)">
          <rect width="14" height="14" rx="3" fill="#3987e5" />
          <rect x="20" width="14" height="14" rx="3" fill="#3987e5" />
          <rect x="40" width="14" height="14" rx="3" fill="#232b29" stroke="#3a4552" />
          <rect x="60" width="14" height="14" rx="3" fill="#232b29" stroke="#3a4552" />
        </g>
        <text x="44" y="204" fill="#858FA7" fontSize="12">
          jobs and notebooks
        </text>
        <rect x="222" y="96" width="170" height="124" rx="10" fill="#161F33" stroke="#26314A" />
        <text x="238" y="122" fontWeight="600">
          Meridian gateway
        </text>
        <text x="238" y="144" fill="#AEB5C6">
          /v1/chat/completions
        </text>
        <text x="238" y="166" fill="#AEB5C6">
          keys, budgets, PII
        </text>
        <text x="238" y="204" fill="#858FA7" fontSize="12">
          prompts stay here
        </text>
        <rect x="28" y="250" width="364" height="70" rx="10" fill="#161F33" stroke="#26314A" />
        <text x="44" y="278" fontWeight="600">
          Students and researchers
        </text>
        <text x="44" y="300" fill="#AEB5C6">
          notebook links, apps, the console
        </text>
        <rect x="28" y="340" width="364" height="48" rx="10" fill="none" stroke="#26314A" />
        <text x="44" y="369" fill="#AEB5C6">
          Datasets and weights on your storage
        </text>
      </g>
      <rect x="452" y="150" width="176" height="120" rx="12" fill="#1a1406" stroke="#F2B441" />
      <g fontFamily={sans} fontSize="13" fill="#ECE6D8">
        <text x="470" y="180" fontWeight="600">
          Meridian Cloud
        </text>
        <text x="470" y="202" fill="#AEB5C6">
          control plane
        </text>
        <text x="470" y="230" fill="#858FA7" fontSize="12">
          health, job specs,
        </text>
        <text x="470" y="248" fill="#858FA7" fontSize="12">
          GPU-hour counts
        </text>
      </g>
      <path d="M185 96 V 22 H 540 V 146" stroke="#F2B441" strokeWidth="1.5" fill="none" markerEnd="url(#ar)" />
      <path d="M392 196 H 448" stroke="#F2B441" strokeWidth="1.5" fill="none" markerEnd="url(#ar)" />
      <rect x="250" y="13" width="102" height="18" fill="#0E1422" />
      <text x="258" y="26" fill="#F2B441" fontFamily={sans} fontSize="12">
        outbound only
      </text>
    </svg>
  )
}
