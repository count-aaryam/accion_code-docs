import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import cytoscape from "cytoscape";
import "./styles.css";

function App() {
  const graphProjectId = new URLSearchParams(window.location.search).get("graph");
  const [health, setHealth] = useState({ loading: true });
  const [projects, setProjects] = useState([]);
  const [form, setForm] = useState({ name: "", github_url: "", branch: "main", access_token: "" });
  const [message, setMessage] = useState("");
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("gemini-free");
  const [enriching, setEnriching] = useState(null);
  const [selectedProject, setSelectedProject] = useState(graphProjectId);
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState(null);
  const [search, setSearch] = useState("");
  const [nodeTypes, setNodeTypes] = useState([]);
  const [edgeTypes, setEdgeTypes] = useState([]);
  const [estimate, setEstimate] = useState(null);
  const [estimating, setEstimating] = useState(false);
  const [summaryWidth, setSummaryWidth] = useState(280);
  const [resizingSummary, setResizingSummary] = useState(false);
  const [wikiTab, setWikiTab] = useState(false);
  const [wikiPlan, setWikiPlan] = useState(null);
  const graphRef = useRef(null);
  const cyRef = useRef(null);

  useEffect(() => {
    fetch("http://localhost:8000/health")
      .then(async (response) => ({ ok: response.ok, ...(await response.json()) }))
      .then(setHealth)
      .catch((error) => setHealth({ ok: false, status: "error", detail: error.message }))
      .finally(() => setHealth((current) => ({ ...current, loading: false })));
  }, []);

  useEffect(() => {
    fetch("http://localhost:8000/models").then((response) => response.json()).then(setModels).catch(() => {});
  }, []);

  useEffect(() => {
    fetch("http://localhost:8000/projects").then((response) => response.ok ? response.json() : []).then((items) => { setProjects(items); if (!graphProjectId && items[0]) setSelectedProject(items[0].id); }).catch(() => {});
  }, []);

  async function loadGraph(projectId, focus = "") {
    if (!projectId) return;
    const params = new URLSearchParams({ depth: "1" });
    if (focus) params.set("focus", focus);
    if (nodeTypes.length) params.set("types", nodeTypes.join(","));
    const response = await fetch(`http://localhost:8000/projects/${projectId}/graph?${params}`);
    if (response.ok) setGraph(await response.json());
  }

  useEffect(() => { loadGraph(selectedProject); }, [selectedProject, nodeTypes.join(",")]);

  useEffect(() => {
    if (!graphRef.current) return;
    cyRef.current?.destroy();
    const cy = cytoscape({ container: graphRef.current, elements: [
      ...graph.nodes.map((node) => ({ data: { id: node.id, label: node.name, kind: node.kind, ...node } })),
      ...graph.edges.map((edge, index) => ({ data: { id: `edge-${index}`, source: edge.source, target: edge.target, label: edge.kind, kind: edge.kind, source_type: edge.source_type } })),
    ], style: [
      { selector: "node", style: { label: "data(label)", "background-color": "#d13b3b", color: "#eee", "font-size": 9, "text-wrap": "ellipsis", "text-max-width": 100 } },
      { selector: 'node[kind="File"]', style: { "background-color": "#777" } },
      { selector: 'node[kind="Class"]', style: { "background-color": "#b84444" } },
      { selector: 'node[kind="Function"], node[kind="Method"]', style: { "background-color": "#e36b6b" } },
      { selector: "edge", style: { width: 1, label: "data(label)", "line-color": "#888", "target-arrow-color": "#888", "target-arrow-shape": "triangle", "font-size": 7, color: "#aaa", "curve-style": "bezier" } },
      { selector: 'edge[source_type="llm"]', style: { "line-style": "dashed", "line-color": "#d13b3b" } },
    ], layout: { name: "cose", animate: false } });
    cy.on("tap", "node", (event) => setSelectedNode(event.target.data()));
    cy.on("dbltap", "node", (event) => loadGraph(selectedProject, event.target.id()));
    if (edgeTypes.length) cy.edges().forEach((edge) => edge.style("display", edgeTypes.includes(edge.data("kind")) ? "element" : "none"));
    cyRef.current = cy;
    return () => cy.destroy();
  }, [graph, selectedProject, edgeTypes.join(",")]);

  useEffect(() => {
    if (!resizingSummary) return undefined;
    const move = (event) => setSummaryWidth(Math.max(220, Math.min(520, window.innerWidth - event.clientX - 32)));
    const stop = () => setResizingSummary(false);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
  }, [resizingSummary]);

  async function submit(event) {
    event.preventDefault();
    setMessage("Creating and cloning...");
    const payload = { ...form, access_token: form.access_token.trim() || null };
    const response = await fetch("http://localhost:8000/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (response.ok) {
      setProjects((current) => [data, ...current]);
      setForm({ name: "", github_url: "", branch: "main", access_token: "" });
      setMessage(`Project ${data.status}.`);
    } else setMessage(data.detail?.[0]?.msg || data.detail || "Could not create project.");
  }

  async function enrich(projectId) {
    await fetch(`http://localhost:8000/projects/${projectId}/enrich?model_id=${selectedModel}`, { method: "POST" });
    setEnriching(projectId);
    const poll = setInterval(async () => {
      const data = await (await fetch(`http://localhost:8000/projects/${projectId}/enrich/status`)).json();
      setProjects((current) => current.map((project) => project.id === projectId ? { ...project, enrichment: data } : project));
      if (["complete", "paused"].includes(data.status)) { clearInterval(poll); setEnriching(null); if (data.status === "complete") loadGraph(projectId); }
    }, 1000);
  }

  async function estimateProject(projectId) {
    setEstimating(true);
    const available = models.filter((model) => model.available);
    const results = await Promise.all(available.map(async (model) => {
      const response = await fetch(`http://localhost:8000/projects/${projectId}/enrich/estimate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_id: model.id, strategy: "bottom-up", mode: "full", sample_size: 30, calibrate: false }) });
      return response.ok ? { model, data: await response.json() } : null;
    }));
    setEstimate({ projectId, results: results.filter(Boolean) });
    setEstimating(false);
  }

  async function planWiki(projectId) {
    const response = await fetch(`http://localhost:8000/projects/${projectId}/wiki/plan`, { method: "POST" });
    if (response.ok) { setWikiPlan(await response.json()); setWikiTab(true); }
  }

  return (
    <main className={`shell ${graphProjectId ? "graph-only" : ""}`}>
      <p className="eyebrow">FastAPI × Neo4j</p>
      <h1>accion-docs</h1>
      <p className="lede">A focused workspace for understanding your codebase.</p>
      <section className="model-picker">
        <p className="label">LLM model</p>
        <select value={selectedModel} onChange={(event) => setSelectedModel(event.target.value)}>
          {models.map((model) => <option key={model.id} value={model.id} disabled={!model.available}>{model.name} {!model.available ? "— Coming soon" : "— Currently available"}</option>)}
        </select>
      </section>
      <section className={`card ${health.ok ? "connected" : ""}`}>
        <span className="dot" />
        <div>
          <p className="label">Database health</p>
          <strong>{health.loading ? "Checking…" : health.ok ? "Neo4j connected" : "Neo4j unavailable"}</strong>
          {!health.loading && !health.ok && <small>{health.detail || "Start Neo4j Desktop and check your .env."}</small>}
        </div>
      </section>
      <form className="form" onSubmit={submit}>
        <input required placeholder="Project name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input required type="url" placeholder="https://github.com/org/repository" value={form.github_url} onChange={(e) => setForm({ ...form, github_url: e.target.value })} />
        <input required placeholder="Branch" value={form.branch} onChange={(e) => setForm({ ...form, branch: e.target.value })} />
        <input type="password" placeholder="Access token (optional)" value={form.access_token} onChange={(e) => setForm({ ...form, access_token: e.target.value })} />
        <button type="submit">Add project</button>
        {message && <small>{message}</small>}
      </form>
      <section className="projects"><p className="label">Projects</p>{projects.map((project) => { const enrichmentComplete = project.enrichment?.status === "complete" || project.enrichment_status === "complete"; return <article className="project" key={project.id}><div><strong>{project.name}</strong>{project.enrichment && <small>{project.enrichment.status} · {project.enrichment.nodes_done}/{project.enrichment.nodes_total} nodes · {project.enrichment.tokens_used} tokens</small>}</div><div><span>{project.status}</span><button className="estimate" disabled={estimating} onClick={() => estimateProject(project.id)}>{estimating ? "Estimating..." : "Estimate"}</button><button className="enrich" disabled={enriching === project.id} onClick={() => enrich(project.id)}>{enriching === project.id ? "Enriching..." : "Enrich"}</button>{enrichmentComplete && <button className="view-graph" onClick={() => window.open(`${window.location.origin}/?graph=${project.id}`, "_blank", "noopener,noreferrer")}>View graph</button>}</div></article>; })}</section>
      {estimate && <section className="estimate-menu"><button className="close-estimate" onClick={() => setEstimate(null)}>×</button><p className="label">Approximate token usage</p>{estimate.results.map(({ model, data }) => <article key={model.id}><strong>{model.name}</strong><span>Approximately {data.totals.input_tokens.p50 + data.totals.output_tokens.p50} tokens</span><span>Likely range: {data.totals.input_tokens.p50 + data.totals.output_tokens.p50}–{data.totals.input_tokens.p90 + data.totals.output_tokens.p90} tokens</span><small>Criteria: {data.meta.confidence} confidence, {data.totals.calls} planned calls, `{data.meta.prompt_version}` prompt, local ~4 characters/token heuristic{data.assumptions?.length ? `, ${data.assumptions.join("; ")}` : ""}.</small></article>)}</section>}
      {wikiTab && wikiPlan && <section className="wiki-panel"><button onClick={() => setWikiTab(false)}>Close wiki plan</button><h2>Page outline</h2>{wikiPlan.pages.map((page) => <div key={page.id}><strong>{page.title}</strong> <small>{page.type} · {page.status}</small></div>)}<h2>Components</h2>{wikiPlan.components.map((component) => <div key={component.id}><strong>{component.name}</strong> · {component.file_count} files · cohesion {component.cohesion} · {component.method}</div>)}<h2>Entry points</h2>{wikiPlan.entry_points.map((entry) => <div key={entry.id}><strong>{entry.kind}</strong> · {entry.file || ""} · {entry.evidence}</div>)}<h2>Diagnostics</h2><pre>{JSON.stringify(wikiPlan.diagnostics, null, 2)}</pre></section>}
      <section className="wiki-controls"><button disabled={!selectedProject} onClick={() => planWiki(selectedProject)}>Plan wiki for selected project</button></section>
      <section className={`graph-section ${graphProjectId ? "graph-only-section" : ""}`}>
        <div className="graph-toolbar"><select value={selectedProject || ""} onChange={(event) => setSelectedProject(event.target.value)}><option value="">Select project</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><input placeholder="Search nodes" value={search} onChange={(event) => { setSearch(event.target.value); cyRef.current?.nodes().forEach((node) => node.style("display", !event.target.value || node.data("label").toLowerCase().includes(event.target.value.toLowerCase()) ? "element" : "none")); }} /></div>
        <div className="type-filters">{["File", "Class", "Function", "Method"].map((type) => <label key={type}><input type="checkbox" checked={nodeTypes.includes(type)} onChange={() => setNodeTypes((current) => current.includes(type) ? current.filter((item) => item !== type) : [...current, type])} />{type}</label>)}<span>Edges:</span>{["CONTAINS", "IMPORTS", "INHERITS", "CALLS"].map((type) => <label key={type}><input type="checkbox" checked={edgeTypes.includes(type)} onChange={() => setEdgeTypes((current) => current.includes(type) ? current.filter((item) => item !== type) : [...current, type])} />{type}</label>)}</div>
        <div className="graph-layout" style={{ gridTemplateColumns: `minmax(0, 1fr) ${selectedNode ? "10px" : "0px"} ${selectedNode ? `${summaryWidth}px` : "0px"}` }}><div ref={graphRef} className="graph-canvas" />{selectedNode && <><div className="graph-splitter" onPointerDown={() => setResizingSummary(true)} title="Drag horizontally to resize summary" /><aside className="node-panel"><button onClick={() => setSelectedNode(null)}>×</button><h3>{selectedNode.name}</h3><small>{selectedNode.kind}</small><p>Language: {selectedNode.language || "unknown"}</p><p>{selectedNode.file_path}:{selectedNode.start_line}-{selectedNode.end_line}</p><p>{selectedNode.summary || "No fresh summary available."}</p></aside></>}</div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
