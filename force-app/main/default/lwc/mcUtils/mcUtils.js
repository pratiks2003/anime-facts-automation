/* Shared helpers for the mobile admin app. */

export function errorText(e) {
    if (!e) return 'Unknown error';
    if (e.body && e.body.message) return e.body.message;
    if (Array.isArray(e.body)) return e.body.map((x) => x.message).join(', ');
    return e.message || String(e);
}

export function num(v) {
    if (v === null || v === undefined || v === '') return '–';
    const n = Number(v);
    if (Number.isNaN(n)) return String(v);
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (Math.abs(n) >= 1e4) return Math.round(n / 1e3) + 'K';
    if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(Math.round(n * 100) / 100);
}

export function ago(value) {
    if (!value) return '';
    const t = new Date(value).getTime();
    if (Number.isNaN(t)) return '';
    const s = Math.round((Date.now() - t) / 1000);
    if (s < 0) return 'in ' + duration(-s);
    if (s < 45) return 'just now';
    return duration(s) + ' ago';
}

export function duration(s) {
    if (s < 60) return s + 's';
    const m = Math.floor(s / 60);
    if (m < 60) return m + 'm';
    const h = Math.floor(m / 60);
    if (h < 24) return h + 'h ' + (m % 60) + 'm';
    return Math.floor(h / 24) + 'd';
}

export function clock(value) {
    if (!value) return '';
    try {
        return new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date(value));
    } catch (e) {
        return '';
    }
}

export function dayClock(value) {
    if (!value) return '';
    try {
        return new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date(value));
    } catch (e) {
        return '';
    }
}

export function verdictClass(v) {
    if (v === 'Winner') return 'pill good';
    if (v === 'Loser') return 'pill bad';
    if (v === 'Average') return 'pill warn';
    if (v === 'Too young') return 'pill live';
    return 'pill';
}

export function statusClass(s) {
    if (s === 'Published') return 'pill good';
    if (s === 'Failed') return 'pill bad';
    if (s === 'Generating' || s === 'Publishing' || s === 'Queued' || s === 'Approved') return 'pill live';
    if (s === 'Rendered') return 'pill warn';
    return 'pill';
}

const AGENTS = {
    Planner: { color: '#ff4d6d', letter: 'P' },
    Editor: { color: '#a78bfa', letter: 'E' },
    Ops: { color: '#ffc24b', letter: 'O' },
    Insights: { color: '#3ddc97', letter: 'I' },
    Strategist: { color: '#3ee0ff', letter: 'S' },
    Showrunner: { color: '#ff8a5c', letter: 'SR' },
    Pipeline: { color: '#7c8db5', letter: 'GH' }
};

export function agentStyle(name) {
    const a = AGENTS[name] || { color: '#7c8db5', letter: (name || '?').charAt(0) };
    return { letter: a.letter, style: `background:${a.color}22;color:${a.color};border-color:${a.color}55` };
}

export function parseJson(v) {
    if (!v) return null;
    if (typeof v === 'object') return v;
    try {
        return JSON.parse(v);
    } catch (e) {
        return null;
    }
}

export function indexBar(index) {
    // 0..3x mapped to 0..100%, median (1.0) marker at 33%
    if (index === null || index === undefined) return { width: 'width:0%', cls: 'bar' };
    const n = Math.max(0, Math.min(3, Number(index)));
    return { width: `width:${Math.round((n / 3) * 100)}%`, cls: n >= 1.25 ? 'bar good' : n <= 0.75 ? 'bar bad' : 'bar' };
}

export function effectList(edits) {
    const out = [];
    const e = parseJson(edits);
    if (!e || typeof e !== 'object') return out;
    Object.keys(e).forEach((role) => {
        const items = Array.isArray(e[role]) ? e[role] : [e[role]];
        items.forEach((it) => {
            const fx = it && typeof it === 'object' ? it.fx : it;
            if (fx) out.push({ key: role + fx + out.length, label: `${role} · ${String(fx).replace(/_/g, ' ')}` + (it && it.text ? ` "${it.text}"` : '') });
        });
    });
    return out;
}
