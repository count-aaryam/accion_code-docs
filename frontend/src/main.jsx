import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import cytoscape from "cytoscape";
import "./styles.css";

function App() {
  const [health, setHealth] = useState({ loading: true });
  const [projects, setProjects] = useState([]);
  const [form, setForm] = useState({ name: "", github_url: "", branch: "main", access_token: "" });
  const [message, setMessage] = useState("");
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("gemini-free");
  const [enriching, setEnriching] = useState(null);
  const [selectedProject, setSelectedProject] = useState(null);
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [selectedNode, setSelectedNode] = useState(null);
  const [search, setSearch] = useState("");
  const [nodeTypes, setNodeTypes] = useState([]);
  const [edgeTypes, setEdgeTypes] = useState([]);
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
    fetch("http://localhost:8000/projects").then((response) => response.ok ? response.json() : []).then((items) => { setProjects(items); if (items[0]) setSelectedProject(items[0].id); }).catch(() => {});
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

  return (
    <main className="shell">
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
      <section className="projects"><p className="label">Projects</p>{projects.map((project) => <article className="project" key={project.id}><div><strong>{project.name}</strong>{project.enrichment && <small>{project.enrichment.status} · {project.enrichment.nodes_done}/{project.enrichment.nodes_total} nodes · {project.enrichment.tokens_used} tokens</small>}</div><div><span>{project.status}</span><button className="enrich" disabled={enriching === project.id} onClick={() => enrich(project.id)}>{enriching === project.id ? "Enriching..." : "Enrich"}</button></div></article>)}</section>
      <section className="graph-section">
        <div className="graph-toolbar"><select value={selectedProject || ""} onChange={(event) => setSelectedProject(event.target.value)}><option value="">Select project</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select><input placeholder="Search nodes" value={search} onChange={(event) => { setSearch(event.target.value); cyRef.current?.nodes().forEach((node) => node.style("display", !event.target.value || node.data("label").toLowerCase().includes(event.target.value.toLowerCase()) ? "element" : "none")); }} /></div>
        <div className="type-filters">{["File", "Class", "Function", "Method"].map((type) => <label key={type}><input type="checkbox" checked={nodeTypes.includes(type)} onChange={() => setNodeTypes((current) => current.includes(type) ? current.filter((item) => item !== type) : [...current, type])} />{type}</label>)}<span>Edges:</span>{["CONTAINS", "IMPORTS", "INHERITS", "CALLS"].map((type) => <label key={type}><input type="checkbox" checked={edgeTypes.includes(type)} onChange={() => setEdgeTypes((current) => current.includes(type) ? current.filter((item) => item !== type) : [...current, type])} />{type}</label>)}</div>
        <div className="graph-layout"><div ref={graphRef} className="graph-canvas" />{selectedNode && <aside className="node-panel"><button onClick={() => setSelectedNode(null)}>×</button><h3>{selectedNode.name}</h3><small>{selectedNode.kind}</small><p>{selectedNode.file_path}:{selectedNode.start_line}-{selectedNode.end_line}</p><p>{selectedNode.summary || "No fresh summary available."}</p></aside>}</div>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
