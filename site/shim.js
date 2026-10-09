// Fait tourner la page du Radar hors de Claude : window.claude est remplacé par Supabase
// (base partagée + connexion par email). La page elle-même n'a pas besoin de changer.
(function () {
  const cfg = window.RADAR_CONFIG || {};
  const sb = window.supabase.createClient(cfg.url, cfg.anonKey);
  let resolveSession;
  const sessionReady = new Promise(r => (resolveSession = r));
  let session = null;

  const cache = new Map();      // collection -> Map(id -> data)
  const loading = new Map();    // collection -> Promise
  const listeners = new Map();  // collection -> Set(fn)
  const fail = e => { console.warn(e); return Object.assign(new Error(e && e.message || "Erreur"), { code: "unavailable" }); };
  const notify = c => (listeners.get(c) || []).forEach(fn => fn());

  function load(c) {
    if (cache.has(c)) return Promise.resolve();
    if (!loading.has(c)) loading.set(c, (async () => {
      const m = new Map();
      for (let from = 0; ; from += 1000) {
        const { data, error } = await sb.from("docs").select("id,data").eq("collection", c).range(from, from + 999);
        if (error) throw fail(error);
        for (const r of data) m.set(r.id, r.data);
        if (data.length < 1000) break;
      }
      cache.set(c, m);
    })());
    return loading.get(c);
  }

  function local(c, id, data, mode) {
    const m = cache.get(c); if (!m) return;
    if (mode === "delete") m.delete(id);
    else if (mode === "update") m.set(id, { ...(m.get(id) || {}), ...data });
    else m.set(id, data);
    notify(c);
  }

  async function write(c, id, data, mode) {
    let r;
    if (mode === "set") r = await sb.from("docs").upsert({ collection: c, id, data, updated_at: new Date().toISOString() });
    else if (mode === "update") r = await sb.rpc("doc_merge", { p_collection: c, p_id: id, p_patch: data });
    else r = await sb.from("docs").delete().eq("collection", c).eq("id", id);
    if (r.error) throw fail(r.error);
    local(c, id, data, mode);
  }

  const docRef = (c, id) => ({
    id,
    set: d => write(c, id, d, "set"),
    update: d => write(c, id, d, "update"),
    delete: () => write(c, id, null, "delete"),
    async get() { await load(c); const d = cache.get(c).get(id); return { id, exists: !!d, data: () => d }; },
  });

  function query(c, order, lim) {
    const snap = () => {
      let rows = [...(cache.get(c) || new Map())].map(([id, data]) => ({ id, data }));
      if (order) rows.sort((a, b) => {
        const x = a.data[order.f] ?? "", y = b.data[order.f] ?? "";
        const r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
        return order.dir === "desc" ? -r : r;
      });
      if (lim) rows = rows.slice(0, lim);
      return { docs: rows.map(r => ({ id: r.id, data: () => r.data })) };
    };
    return {
      orderBy: (f, dir = "asc") => query(c, { f, dir }, lim),
      limit: n => query(c, order, n),
      doc: id => docRef(c, id),
      async add(d) { const id = crypto.randomUUID().replace(/-/g, "").slice(0, 20); await write(c, id, d, "set"); return docRef(c, id); },
      onSnapshot(cb, onErr) {
        const run = () => cb(snap());
        if (!listeners.has(c)) listeners.set(c, new Set());
        listeners.get(c).add(run);
        load(c).then(run).catch(e => onErr && onErr(e));
        return () => listeners.get(c).delete(run);
      },
    };
  }

  const db = {
    collection: c => query(c),
    doc: path => { const i = path.lastIndexOf("/"); return docRef(path.slice(0, i), path.slice(i + 1)); },
  };

  const names = {};
  const user = {
    id: async () => session.user.id,
    isOwner: () => false,
    async profiles(ids) {
      const want = ids.filter(i => !(i in names));
      if (want.length) {
        const { data } = await sb.from("members").select("user_id,name").in("user_id", want.filter(i => /^[0-9a-f-]{36}$/.test(i)));
        for (const i of want) names[i] = "";
        for (const r of data || []) names[r.user_id] = r.name;
      }
      return Object.fromEntries(ids.map(i => [i, { name: names[i] || "" }]));
    },
  };

  window.claude = { use: async n => { await sessionReady; return n === "db" ? db : n === "user" ? user : null; } };

  function startRealtime() {
    sb.channel("docs").on("postgres_changes", { event: "*", schema: "public", table: "docs" }, p => {
      const row = p.eventType === "DELETE" ? p.old : p.new;
      if (!row || !row.collection || !cache.has(row.collection)) return;
      local(row.collection, row.id, p.new && p.new.data, p.eventType === "DELETE" ? "delete" : "set");
    }).subscribe();
  }

  // ----- Connexion par email (lien magique) -----
  const gate = document.getElementById("login");
  const msg = document.getElementById("login-msg");
  async function enter(s) {
    session = s;
    await sb.rpc("claim_member");
    const { data } = await sb.from("members").select("name").ilike("email", s.user.email).maybeSingle();
    if (!data) {
      msg.textContent = "Ton adresse " + s.user.email + " n'est pas encore dans l'équipe. Demande à Vincent de l'ajouter.";
      gate.hidden = false; return;
    }
    try { if (!localStorage.getItem("porteur")) localStorage.setItem("porteur", data.name); } catch {}
    gate.hidden = true;
    document.getElementById("whoami").textContent = data.name;
    startRealtime();
    resolveSession();
  }
  document.getElementById("login-form").addEventListener("submit", async e => {
    e.preventDefault();
    const email = document.getElementById("login-email").value.trim();
    msg.textContent = "Envoi du lien…";
    const { error } = await sb.auth.signInWithOtp({ email, options: { emailRedirectTo: location.origin + location.pathname, shouldCreateUser: true } });
    msg.textContent = error ? "Envoi impossible : " + error.message : "C'est envoyé. Ouvre le lien reçu à " + email + " sur cet appareil.";
  });
  // Reprise des données exportées de la page Claude (fichier JSON {collection: {id: data}}).
  document.getElementById("restore").addEventListener("change", async e => {
    const f = e.target.files[0]; if (!f || !session) return;
    const who = document.getElementById("whoami"), keep = who.textContent;
    try {
      const all = JSON.parse(await f.text());
      const rows = Object.entries(all).flatMap(([c, ds]) => Object.entries(ds).map(([id, data]) => ({ collection: c, id, data })));
      if (!confirm("Importer " + rows.length + " éléments ? Ceux qui existent déjà seront remplacés.")) return;
      for (let i = 0; i < rows.length; i += 50) {
        who.textContent = "Import " + Math.min(i + 50, rows.length) + " / " + rows.length + "…";
        const { error } = await sb.from("docs").upsert(rows.slice(i, i + 50));
        if (error) throw error;
      }
      location.reload();
    } catch (x) { alert("Import interrompu : " + (x.message || x)); who.textContent = keep; }
  });
  document.getElementById("logout").addEventListener("click", async () => { await sb.auth.signOut(); location.reload(); });
  sb.auth.getSession().then(({ data }) => { if (data.session) enter(data.session); else gate.hidden = false; });
  sb.auth.onAuthStateChange((ev, s) => { if (ev === "SIGNED_IN" && s && !session) enter(s); });
})();
