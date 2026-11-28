import { LightningElement, track } from 'lwc';
import getOverview from '@salesforce/apex/MissionControlController.getOverview';
import resolveItem from '@salesforce/apex/MissionControlController.resolveItem';
import askShowrunner from '@salesforce/apex/MissionControlController.askShowrunner';

const REFRESH_MS = 60000;
const STEPS = ['Planned', 'Rendering', 'Quality check', 'Posting', 'Live'];
const AGENTS = {
    planner: { initial: 'P', color: 'agent agent_planner', label: 'Planner' },
    editor: { initial: 'E', color: 'agent agent_editor', label: 'Editor' },
    ops: { initial: 'O', color: 'agent agent_ops', label: 'Ops' },
    insights: { initial: 'I', color: 'agent agent_insights', label: 'Insights' },
    strategist: { initial: 'S', color: 'agent agent_strategist', label: 'Strategist' },
    showrunner: { initial: 'SR', color: 'agent agent_showrunner', label: 'Showrunner' }
};
const VERDICT = {
    Winner: 'verdict verdict_win',
    Average: 'verdict verdict_avg',
    Loser: 'verdict verdict_lose',
    'Too young': 'verdict verdict_young',
    'No data': 'verdict verdict_none'
};
const NAV = [
    { id: 'top', label: 'Live' },
    { id: 'reels', label: 'Reels' },
    { id: 'agents', label: 'Agents' },
    { id: 'strategy', label: 'Strategy' }
];

function ago(value, now) {
    if (!value) {
        return '—';
    }
    const s = Math.max(0, Math.round((now - new Date(value).getTime()) / 1000));
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
}

function compact(n) {
    if (n === null || n === undefined) return '—';
    const v = Number(n);
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e4) return `${Math.round(v / 1e3)}K`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
    return `${Math.round(v)}`;
}

function pretty(value) {
    if (value === null || value === undefined || value === '') return '—';
    try {
        const parsed = JSON.parse(value);
        if (typeof parsed === 'object' && parsed !== null) {
            if (Array.isArray(parsed)) return parsed.join(', ');
            return Object.entries(parsed)
                .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'object' ? JSON.stringify(v) : v}`)
                .join(' · ');
        }
        return String(parsed);
    } catch (e) {
        return String(value);
    }
}

function agentFor(name) {
    const key = String(name || 'showrunner').toLowerCase();
    return AGENTS[key] || { initial: key.charAt(0).toUpperCase(), color: 'agent agent_showrunner', label: name };
}

export default class MissionControl extends LightningElement {
    @track data;
    loading = true;
    refreshing = false;
    error;
    now = Date.now();
    expanded = {};
    question = '';
    answer;
    asking = false;
    askError;
    refreshTimer;
    clockTimer;

    connectedCallback() {
        this.load();
        /* eslint-disable @lwc/lwc/no-async-operation */
        this.refreshTimer = setInterval(() => this.load(true), REFRESH_MS);
        this.clockTimer = setInterval(() => {
            this.now = Date.now();
        }, 1000);
        /* eslint-enable @lwc/lwc/no-async-operation */
    }

    disconnectedCallback() {
        clearInterval(this.refreshTimer);
        clearInterval(this.clockTimer);
    }

    async load(silent = false) {
        if (!silent) this.refreshing = true;
        try {
            this.data = await getOverview();
            this.error = undefined;
        } catch (e) {
            this.error = (e && e.body && e.body.message) || (e && e.message) || 'Could not load';
        } finally {
            this.loading = false;
            this.refreshing = false;
        }
    }

    handleRefresh() {
        this.load();
    }

    // ------------------------------------------------------------ header / autopilot

    get autopilot() {
        return (this.data && this.data.autopilot) || {};
    }
    get autopilotOn() {
        return this.autopilot.automationEnabled && !this.autopilot.dryRun;
    }
    get pillClass() {
        if (!this.autopilot.automationEnabled) return 'pill pill_off';
        return this.autopilot.dryRun ? 'pill pill_warn' : 'pill pill_on';
    }
    get pillLabel() {
        if (!this.autopilot.automationEnabled) return 'Autopilot off';
        return this.autopilot.dryRun ? 'Autopilot · dry run' : 'Autopilot engaged';
    }
    get cadence() {
        return `${this.autopilot.reelsPerDay || 0} Reels / day`;
    }
    get refreshClass() {
        return this.refreshing ? 'icon-btn spinning' : 'icon-btn';
    }
    get updatedLabel() {
        return this.data ? `Updated ${ago(this.data.generatedAt, this.now)}` : '';
    }

    get countdown() {
        const at = this.autopilot.nextSlotAt;
        if (!at) return { h: '--', m: '--', s: '--' };
        const diff = Math.max(0, Math.floor((new Date(at).getTime() - this.now) / 1000));
        const pad = (n) => String(n).padStart(2, '0');
        return { h: pad(Math.floor(diff / 3600)), m: pad(Math.floor((diff % 3600) / 60)), s: pad(diff % 60) };
    }
    get nextSlotLabel() {
        const at = this.autopilot.nextSlotAt;
        if (!at) return 'No slot scheduled';
        const t = new Date(at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' });
        return `${this.autopilot.nextSlot} slot · ${t} IST`;
    }
    get schedule() {
        return (this.autopilot.schedule || []).map((s) => ({
            ...s,
            className: s.done ? 'slot slot_done' : s.name === this.autopilot.nextSlot ? 'slot slot_next' : 'slot'
        }));
    }

    get active() {
        return this.autopilot.active;
    }
    get steps() {
        const current = this.active ? this.active.step : -1;
        return STEPS.map((label, i) => ({
            label,
            key: label,
            className: i < current ? 'step step_done' : i === current ? 'step step_now' : 'step',
            num: i + 1
        }));
    }
    get activeTitle() {
        const a = this.active;
        if (!a) return '';
        return [a.show, a.format].filter(Boolean).join(' · ');
    }

    // ------------------------------------------------------------ inbox

    get inbox() {
        return ((this.data && this.data.inbox) || []).map((i) => ({
            ...i,
            when: ago(i.createdDate, this.now),
            className: `inbox-item sev_${(i.severity || 'info').toLowerCase()}`,
            who: agentFor(i.agent)
        }));
    }
    get hasInbox() {
        return this.inbox.length > 0;
    }
    get inboxCount() {
        return this.inbox.length;
    }

    async handleResolve(event) {
        const id = event.currentTarget.dataset.id;
        try {
            await resolveItem({ activityId: id });
            this.load(true);
        } catch (e) {
            this.error = (e && e.body && e.body.message) || 'Could not resolve';
        }
    }

    // ------------------------------------------------------------ KPIs

    get kpis() {
        const k = (this.data && this.data.kpis) || {};
        const idx = k.avgIndex;
        return [
            { key: 'views', label: 'Views · 7 days', value: compact(k.views7d), sub: `${k.published || 0} Reels live` },
            { key: 'watch', label: 'Avg watch', value: k.avgWatchSeconds ? `${k.avgWatchSeconds}s` : '—', sub: 'per view' },
            {
                key: 'index',
                label: 'Performance',
                value: idx ? `${idx}×` : '—',
                sub: idx ? (idx >= 1 ? 'above channel median' : 'below channel median') : 'scoring…'
            },
            { key: 'wl', label: 'Winners / losers', value: `${k.winners || 0} / ${k.losers || 0}`, sub: `${k.scored || 0} scored · 30d` },
            { key: 'posts', label: 'Posted · 24h', value: `${k.posts24h || 0}/${k.maxPerDay || 6}`, sub: 'account-safe limit' }
        ];
    }

    // ------------------------------------------------------------ Reels

    get reels() {
        return ((this.data && this.data.reels) || []).map((r) => {
            const idx = r.index === null || r.index === undefined ? null : Number(r.index);
            const verdict = r.verdict || (r.status === 'Published' ? 'Too young' : null);
            const pct = idx === null ? 0 : Math.min(100, Math.round((idx / 2) * 100));
            return {
                ...r,
                verdictLabel: verdict || r.status,
                verdictClass: verdict ? VERDICT[verdict] || 'verdict' : 'verdict verdict_status',
                indexLabel: idx === null ? '' : `${idx.toFixed(2)}×`,
                hasIndex: idx !== null,
                barStyle: `width:${pct}%`,
                barClass: idx === null ? 'bar-fill' : idx >= 1.25 ? 'bar-fill fill_win' : idx <= 0.75 ? 'bar-fill fill_lose' : 'bar-fill',
                viewsLabel: compact(r.views),
                watchLabel: r.watchSeconds ? `${r.watchSeconds}s` : '—',
                sharesLabel: compact(r.shares),
                hasCover: !!r.cover,
                effectChips: (r.effects || []).map((e) => ({ key: e, label: e.replace(/_/g, ' ') })),
                hasEffects: (r.effects || []).length > 0,
                planner: r.plannedBy ? r.plannedBy.replace('Showrunner ', '') : 'Autopilot',
                when: ago(r.publishedAt || r.createdDate, this.now),
                editLabel: r.editScore ? `${r.editScore}/10` : '—',
                directionLabel: r.directionScore ? `${r.directionScore}/10` : '—',
                hasLink: !!r.permalink,
                fallbackLetter: (r.show || r.format || '?').charAt(0)
            };
        });
    }
    get hasReels() {
        return this.reels.length > 0;
    }

    // ------------------------------------------------------------ agents

    get feed() {
        return ((this.data && this.data.feed) || []).map((a) => {
            const who = agentFor(a.agent);
            const open = !!this.expanded[a.id];
            const text = a.details || '';
            return {
                ...a,
                who,
                when: ago(a.createdDate, this.now),
                summary: text.length > 220 && !open ? `${text.slice(0, 220)}…` : text,
                canExpand: text.length > 220,
                toggleLabel: open ? 'Less' : 'More',
                className: a.severity === 'critical' ? 'feed-item feed_crit' : a.severity === 'warning' ? 'feed-item feed_warn' : 'feed-item'
            };
        });
    }
    handleToggle(event) {
        const id = event.currentTarget.dataset.id;
        this.expanded = { ...this.expanded, [id]: !this.expanded[id] };
    }

    get requests() {
        return ((this.data && this.data.requests) || []).map((r) => ({ ...r, when: ago(r.createdDate, this.now), who: agentFor(r.agent) }));
    }
    get hasRequests() {
        return this.requests.length > 0;
    }

    // ------------------------------------------------------------ strategy

    get playbook() {
        return ((this.data && this.data.playbook) || []).map((p) => ({ ...p, display: pretty(p.value), when: ago(p.updated, this.now) }));
    }
    get hasPlaybook() {
        return this.playbook.length > 0;
    }
    get changes() {
        return ((this.data && this.data.changes) || []).map((c) => ({
            ...c,
            when: ago(c.createdDate, this.now),
            oldDisplay: pretty(c.oldValue),
            newDisplay: pretty(c.newValue),
            statusClass:
                c.status === 'Kept' ? 'badge badge_good' : c.status === 'Rolled back' ? 'badge badge_bad' : 'badge badge_live',
            evidence:
                c.baseline !== null && c.baseline !== undefined
                    ? `baseline ${Number(c.baseline).toFixed(2)}× → ${
                          c.result !== null && c.result !== undefined ? `${Number(c.result).toFixed(2)}×` : 'measuring'
                      } · ${c.reelsSince || 0} Reels`
                    : ''
        }));
    }
    get hasChanges() {
        return this.changes.length > 0;
    }

    get trends() {
        return ((this.data && this.data.trends) || []).map((t, i) => ({
            ...t,
            key: `${i}`,
            heatStyle: `width:${Math.min(100, (Number(t.heat) || 0) * 10)}%`,
            heatLabel: t.heat ? `${t.heat}` : '—'
        }));
    }
    get hasTrends() {
        return this.trends.length > 0;
    }

    // ------------------------------------------------------------ health

    get health() {
        const h = (this.data && this.data.health) || {};
        const fresh = (value, hours) => (value && this.now - new Date(value).getTime() < hours * 3600000 ? 'dot dot_ok' : 'dot dot_warn');
        return [
            { key: 'm', label: 'Instagram metrics', value: ago(h.metricsUpdatedAt, this.now), dot: fresh(h.metricsUpdatedAt, 12) },
            { key: 't', label: 'Trend research', value: ago(h.trendsUpdatedAt, this.now), dot: fresh(h.trendsUpdatedAt, 10) },
            { key: 'f', label: 'Failed jobs · 24h', value: `${h.failedJobs24h || 0}`, dot: h.failedJobs24h ? 'dot dot_warn' : 'dot dot_ok' },
            { key: 'k', label: 'Verified facts', value: `${h.facts || 0}`, dot: 'dot dot_ok' },
            { key: 'l', label: 'Active lessons', value: `${h.lessons || 0}`, dot: 'dot dot_ok' }
        ];
    }

    // ------------------------------------------------------------ ask

    handleQuestion(event) {
        this.question = event.target.value;
    }
    handleAskKey(event) {
        if (event.key === 'Enter') this.handleAsk();
    }
    async handleAsk() {
        if (!this.question || this.asking) return;
        this.asking = true;
        this.answer = undefined;
        this.askError = undefined;
        try {
            this.answer = await askShowrunner({ question: this.question });
        } catch (e) {
            this.askError = (e && e.body && e.body.message) || 'The Showrunner is busy, try again.';
        } finally {
            this.asking = false;
        }
    }
    handleSuggestion(event) {
        this.question = event.currentTarget.dataset.q;
        this.handleAsk();
    }
    get suggestions() {
        return [
            { key: '1', q: 'How did our last 5 Reels perform and why?' },
            { key: '2', q: 'What will you post next and why?' },
            { key: '3', q: 'What is the biggest problem in our system right now?' }
        ];
    }
    get askLabel() {
        return this.asking ? 'Thinking…' : 'Ask';
    }

    // ------------------------------------------------------------ nav

    get nav() {
        return NAV;
    }
    handleNav(event) {
        const el = this.template.querySelector(`[data-section="${event.currentTarget.dataset.id}"]`);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}
