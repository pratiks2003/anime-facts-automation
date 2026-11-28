import { LightningElement } from 'lwc';
import getGrowth from '@salesforce/apex/AdminAppController.getGrowth';
import { ago, errorText, indexBar, parseJson } from 'c/mcUtils';

const VIEWS = [
    { id: 'score', label: 'Scoreboard' },
    { id: 'strategy', label: 'Strategy' },
    { id: 'requests', label: 'Agent requests' },
    { id: 'trends', label: 'Trends' }
];
const GROUPS = [
    { key: 'by_show', title: 'Shows' },
    { key: 'by_format', title: 'Formats' },
    { key: 'by_slot', title: 'Posting slots' },
    { key: 'by_effect', title: 'Edit effects' },
    { key: 'by_audio', title: 'Audio' },
    { key: 'by_style', title: 'Style' },
    { key: 'by_planner', title: 'Who planned it' }
];

export default class McGrowth extends LightningElement {
    data;
    error;
    view = 'score';
    open = {};

    connectedCallback() {
        this.load();
    }
    async load() {
        try {
            this.data = await getGrowth();
            this.error = undefined;
        } catch (e) {
            this.error = errorText(e);
        }
    }

    get ready() { return !!this.data; }
    get views() { return VIEWS.map((v) => ({ ...v, cls: v.id === this.view ? 'chip on' : 'chip' })); }
    pick(event) { this.view = event.currentTarget.dataset.id; }
    get isScore() { return this.view === 'score'; }
    get isStrategy() { return this.view === 'strategy'; }
    get isRequests() { return this.view === 'requests'; }
    get isTrends() { return this.view === 'trends'; }

    get sb() { return (this.data && this.data.scoreboard) || {}; }
    get freshness() {
        const h = this.sb.metrics_freshness_hours;
        if (h === null || h === undefined) return 'No metrics yet';
        return `Views updated ${h} h ago`;
    }
    get freshCls() {
        const h = this.sb.metrics_freshness_hours;
        return h === null || h === undefined ? 'pill' : h <= 8 ? 'pill good' : 'pill warn';
    }
    get scored() { return this.sb.scored_reels || 0; }
    get tooYoung() { return (this.sb.too_young_to_judge || []).join(', '); }
    get groups() {
        return GROUPS.map((g) => {
            const rows = (this.sb[g.key] || []).map((r, i) => {
                const bar = indexBar(r.avg_index);
                const status = r.status && r.status !== 'open' ? r.status : null;
                return {
                    key: g.key + i, name: r.name, n: r.n, avg: r.avg_index, wins: r.wins || 0, losses: r.losses || 0,
                    barCls: bar.cls, barStyle: bar.width, status,
                    statusCls: status && status.startsWith('DROPPED') ? 'pill bad' : 'pill good',
                    facts: r.verified_facts !== undefined ? `${r.verified_facts} facts` : null
                };
            });
            return { ...g, rows, has: rows.length > 0 };
        }).filter((g) => g.has);
    }
    get hasGroups() { return this.groups.length > 0; }
    get inventory() {
        return (this.sb.fact_inventory || []).map((f, i) => ({
            key: 'f' + i, show: f.show, facts: f.verified_facts, recent: f.reels_last_3_days, risk: f.risk,
            cls: f.risk === 'ok' ? 'pill good' : String(f.risk).startsWith('THIN') ? 'pill bad' : 'pill warn',
            riskLabel: f.risk === 'ok' ? 'ok' : String(f.risk).split(':')[0]
        }));
    }
    get hasInventory() { return this.inventory.length > 0; }

    get playbook() {
        return ((this.data && this.data.playbook) || []).map((p) => ({ ...p, label: String(p.key).replace(/_/g, ' '), pretty: pretty(p.value) }));
    }
    get hours() {
        const d = this.data && this.data.data && this.data.data.audience_online_hours;
        const by = d && d.followers_online_by_hour;
        if (!by) return null;
        const vals = Array.from({ length: 24 }, (_, h) => Number(by[String(h)] || 0));
        const max = Math.max(1, ...vals);
        return vals.map((v, h) => ({ key: 'h' + h, h, style: `height:${Math.max(4, Math.round((v / max) * 100))}%`, label: h % 6 === 0 ? String(h) : '' }));
    }
    get hasHours() { return !!this.hours; }
    get trendingAudio() {
        const t = this.data && this.data.data && this.data.data.trending_audio;
        return t ? `${t.tracks_available || 0} licensed trending tracks · updated ${ago(t.fetched_at)}` : 'Trending audio list arrives with the next morning sync.';
    }
    get changes() {
        return ((this.data && this.data.changes) || []).map((c, i) => ({
            ...c, key: 'c' + i, when: ago(c.when), label: String(c.setting || '').replace(/_/g, ' '),
            cls: c.status === 'Kept' ? 'pill good' : c.status === 'Rolled back' ? 'pill bad' : 'pill live',
            fromP: pretty(c.from), toP: pretty(c.to),
            result: c.result !== null && c.result !== undefined ? `${c.baseline !== null && c.baseline !== undefined ? c.baseline : '–'}× → ${c.result}× over ${c.reelsSince || 0} Reels` : `baseline ${c.baseline !== null && c.baseline !== undefined ? c.baseline : '–'}× · measuring`
        }));
    }
    get hasChanges() { return this.changes.length > 0; }
    get review() { return this.data && this.data.dailyReview; }
    get reviewWhen() { return this.review ? ago(this.review.when) : ''; }

    get requests() {
        return ((this.data && this.data.requests) || []).map((r, i) => ({
            ...r, key: 'r' + i, when: ago(r.when), cls: r.type === 'Feature request' ? 'pill accent' : 'pill live',
            short: String(r.detail || '').slice(0, 400)
        }));
    }
    get hasRequests() { return this.requests.length > 0; }

    get trends() {
        return ((this.data && this.data.trends) || []).map((t, i) => {
            const heat = Math.max(0, Math.min(10, Number(t.heat) || 0));
            return { key: 't' + i, topic: t.topic, anime: t.anime, heat, style: `width:${heat * 10}%` };
        });
    }
    get hasTrends() { return this.trends.length > 0; }
    get trendsWhen() { return this.data && this.data.trendsAt ? 'updated ' + ago(this.data.trendsAt) : ''; }
}

function pretty(v) {
    const p = parseJson(v);
    if (p === null || p === undefined) return v === undefined || v === null ? '–' : String(v);
    if (Array.isArray(p)) return p.length ? p.join(', ') : '(none)';
    if (typeof p === 'object') {
        const keys = Object.keys(p);
        return keys.length ? keys.map((k) => `${k}: ${typeof p[k] === 'object' ? JSON.stringify(p[k]) : p[k]}`).join(' · ') : '(default)';
    }
    return String(p);
}
