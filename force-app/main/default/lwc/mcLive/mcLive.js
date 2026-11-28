import { LightningElement, api } from 'lwc';
import getReelStory from '@salesforce/apex/AdminAppController.getReelStory';
import listStoryReels from '@salesforce/apex/AdminAppController.listStoryReels';
import { agentStyle, clock, dayClock, effectList, errorText, num, statusClass, verdictClass } from 'c/mcUtils';

const FAST_MS = 8000;
const SLOW_MS = 60000;
const KIND = {
    plan: { icon: '🧭', label: 'Plan' },
    script: { icon: '✍️', label: 'Script' },
    shots: { icon: '🖼️', label: 'Shots' },
    qa: { icon: '🔍', label: 'Quality check' },
    cover: { icon: '🎴', label: 'Cover' },
    music: { icon: '🎵', label: 'Music' },
    gate: { icon: '🚦', label: 'Gate' },
    publish: { icon: '🚀', label: 'Posting' },
    growth: { icon: '📈', label: 'Growth' },
    stage: { icon: '⚙️', label: 'Pipeline' },
    error: { icon: '⚠️', label: 'Error' }
};
const LONG = 240;

export default class McLive extends LightningElement {
    _reelId;
    story;
    reels = [];
    error;
    loading = true;
    timer;
    seen = new Set();
    firstLoad = true;
    expanded = {};

    @api
    get reelId() {
        return this._reelId;
    }
    set reelId(v) {
        if (v && v !== this._reelId) {
            this._reelId = v;
            this.reset();
            if (this.isConnected) this.load();
        } else if (!this._reelId) {
            this._reelId = v;
        }
    }

    connectedCallback() {
        this.load();
        this.loadReels();
    }
    disconnectedCallback() {
        clearTimeout(this.timer);
    }

    reset() {
        this.story = undefined;
        this.seen = new Set();
        this.firstLoad = true;
        this.expanded = {};
        this.loading = true;
    }

    async load() {
        clearTimeout(this.timer);
        try {
            const s = await getReelStory({ externalId: this._reelId || null });
            this.story = s;
            if (!this._reelId && s && s.reel) this._reelId = s.reel.id;
            this.error = undefined;
        } catch (e) {
            this.error = errorText(e);
        } finally {
            this.loading = false;
        }
        const fast = this.story && this.story.inProgress;
        this.timer = setTimeout(() => this.load(), fast ? FAST_MS : SLOW_MS); // eslint-disable-line @lwc/lwc/no-async-operation
    }

    async loadReels() {
        try {
            this.reels = await listStoryReels();
        } catch (e) {
            this.reels = [];
        }
    }

    pickReel(event) {
        const id = event.currentTarget.dataset.id;
        if (id && id !== this._reelId) {
            this._reelId = id;
            this.reset();
            this.load();
        }
    }

    toggle(event) {
        const k = event.currentTarget.dataset.key;
        this.expanded = { ...this.expanded, [k]: !this.expanded[k] };
    }

    refresh() {
        this.load();
        this.loadReels();
    }

    // ------------------------------------------------------------ view model

    get found() { return this.story && this.story.found; }
    get notFound() { return this.story && !this.story.found; }
    get reel() { return (this.story && this.story.reel) || {}; }
    get inProgress() { return this.story && this.story.inProgress; }

    get chips() {
        return (this.reels || []).map((r) => ({
            ...r,
            cls: r.id === this._reelId ? 'chip on' : 'chip',
            label: `${r.slot || 'Manual'} · ${r.show || r.id}`
        }));
    }

    get headTitle() { return this.reel.show || this.reel.format || this.reel.id; }
    get statusCls() { return statusClass(this.reel.status); }
    get verdictCls() { return verdictClass(this.reel.verdict); }
    get liveLabel() { return this.inProgress ? 'Agents working' : 'Story'; }
    get liveCls() { return this.inProgress ? 'pill live' : 'pill'; }
    get whenLabel() { return dayClock(this.reel.publishedAt || this.reel.scheduledFor || this.reel.createdDate); }

    get steps() {
        const labels = (this.story && this.story.steps) || [];
        const step = this.story ? this.story.step || 0 : 0;
        const failed = this.reel.status === 'Failed';
        return labels.map((label, i) => {
            let cls = 'step';
            if (i < step || (i === step && this.reel.status === 'Published')) cls += ' done';
            else if (i === step) cls += failed ? ' fail' : this.inProgress ? ' now' : ' done';
            return { key: label, label, cls, n: i + 1 };
        });
    }
    get currentStep() {
        const labels = (this.story && this.story.steps) || [];
        return labels[(this.story && this.story.step) || 0] || '';
    }
    get thinkingLine() {
        if (this.reel.status === 'Failed') return 'This Reel failed - Ops decides what happens next.';
        if (!this.inProgress) return this.reel.status === 'Published' ? 'Posted. Here is how it was made.' : 'Here is how it was made.';
        const stage = this.story && this.story.stage;
        return stage ? `Working on: ${stage}` : `Working on: ${this.currentStep}`;
    }

    get plan() { return this.story && this.story.plan; }
    get hasPlan() { return !!this.plan; }
    get planBy() { return this.plan ? this.plan.by || 'Showrunner' : ''; }
    get planEffects() { return this.plan ? effectList(this.plan.edits) : []; }
    get hasPlanEffects() { return this.planEffects.length > 0; }
    get planStyle() {
        const st = this.plan && this.plan.style;
        if (!st || typeof st !== 'object') return [];
        return Object.keys(st).map((k) => ({ key: k, label: `${k.replace(/_/g, ' ')}: ${st[k]}` }));
    }
    get hasPlanStyle() { return this.planStyle.length > 0; }
    get evidence() {
        const e = this.plan && this.plan.scoreboard_at_plan;
        if (!e) return null;
        return { avg: e.avg_index, n: e.n, last: (e.last_verdicts || []).join(' → ') || '–', status: e.status, facts: e.verified_facts };
    }
    get briefKey() { return 'brief'; }
    get briefLong() { return this.plan && this.plan.notes && this.plan.notes.length > LONG; }
    get briefText() {
        const t = (this.plan && this.plan.notes) || '';
        return this.expanded.brief || t.length <= LONG ? t : t.slice(0, LONG) + '…';
    }
    get briefToggle() { return this.expanded.brief ? 'Show less' : 'Read the full brief'; }

    get events() {
        const raw = (this.story && this.story.events) || [];
        const keys = [];
        const list = raw.map((e, i) => {
            const key = `${e.source}|${e.t}|${e.title}|${i}`;
            keys.push(key);
            const isAgent = e.source === 'agent';
            const meta = KIND[e.kind] || KIND.stage;
            const av = agentStyle(isAgent ? e.agent : 'Pipeline');
            const detail = e.detail ? String(e.detail) : '';
            const long = detail.length > LONG;
            const isNew = !this.firstLoad && !this.seen.has(key);
            return {
                key,
                isAgent,
                who: isAgent ? e.agent : meta.label,
                icon: isAgent ? av.letter : meta.icon,
                avatarStyle: isAgent ? av.style : 'background:rgba(255,255,255,0.06);border-color:rgba(255,255,255,0.12)',
                title: e.title || meta.label,
                time: clock(e.t),
                detail: this.expanded[key] || !long ? detail : detail.slice(0, LONG) + '…',
                hasDetail: !!detail,
                long,
                toggleLabel: this.expanded[key] ? 'Show less' : 'Show the reasoning',
                hasScore: e.score !== null && e.score !== undefined,
                score: e.score,
                scoreStyle: ring(e.score),
                hasScore2: e.score2 !== null && e.score2 !== undefined,
                score2: e.score2,
                score2Style: ring(e.score2),
                isCover: e.kind === 'cover' && !!this.reel.cover,
                cover: this.reel.cover,
                runUrl: e.runUrl,
                sevBad: e.severity === 'critical' || e.kind === 'error',
                cls: 'ev' + (isNew ? ' new' : '') + (e.kind === 'error' || e.severity === 'critical' ? ' bad' : '')
            };
        });
        if (list.length && this.inProgress) list[list.length - 1].cls += ' latest';
        // remember what we've shown (animate only genuinely new events)
        Promise.resolve().then(() => {
            keys.forEach((k) => this.seen.add(k));
            this.firstLoad = false;
        });
        return list;
    }
    get hasEvents() { return this.events.length > 0; }

    get report() { return this.story && this.story.report; }
    get hasReport() { return !!this.report; }
    get qa() { return (this.report && this.report.visual_qa) || {}; }
    get editScore() { return this.qa.edit_score; }
    get editStyle() { return ring(this.qa.edit_score); }
    get directionScore() { return this.qa.direction_score; }
    get directionStyle() { return ring(this.qa.direction_score); }
    get hasEdit() { return this.qa.edit_score !== null && this.qa.edit_score !== undefined; }
    get hasDirection() { return this.qa.direction_score !== null && this.qa.direction_score !== undefined; }
    get notes() {
        const out = [];
        (this.qa.direction_notes || []).forEach((n, i) => out.push({ key: 'd' + i, text: n, cls: 'note' }));
        (this.qa.edit_notes || []).forEach((n, i) => out.push({ key: 'e' + i, text: n, cls: 'note' }));
        (this.qa.issues || []).forEach((n, i) => out.push({ key: 'i' + i, text: n, cls: 'note bad' }));
        (this.qa.fixed_issues || []).forEach((n, i) => out.push({ key: 'f' + i, text: 'Fixed: ' + n, cls: 'note good' }));
        return out.slice(0, 12);
    }
    get hasNotes() { return this.notes.length > 0; }
    get qaSummary() { return this.qa.summary; }
    get factLine() {
        const f = this.report && this.report.fact_check;
        if (!f) return null;
        return f.pass === false ? 'Fact check: problems flagged' : 'Fact check: passed';
    }
    get factCls() {
        const f = this.report && this.report.fact_check;
        return f && f.pass === false ? 'pill bad' : 'pill good';
    }
    get effectsUsed() {
        return ((this.report && this.report.effects_used) || []).map((e) => ({ key: e, label: String(e).replace(/_/g, ' ') }));
    }
    get hasEffectsUsed() { return this.effectsUsed.length > 0; }
    get audioLine() {
        const t = this.report && this.report.trending_audio;
        if (t && t.title) return `Trending audio: ${t.title}${t.artist ? ' – ' + t.artist : ''}`;
        return this.reel.music ? `Music: ${this.reel.music}` : null;
    }
    get duration() { return this.report && this.report.duration_seconds ? Math.round(this.report.duration_seconds) + 's' : null; }

    get isPosted() { return this.reel.status === 'Published'; }
    get results() {
        const r = this.reel;
        return [
            { k: 'v', label: 'views', value: num(r.views) },
            { k: 'w', label: 'avg watch', value: r.watchSeconds ? r.watchSeconds + 's' : '–' },
            { k: 's', label: 'shares', value: num(r.shares) },
            { k: 'sv', label: 'saves', value: num(r.saves) },
            { k: 'l', label: 'likes', value: num(r.likes) },
            { k: 'c', label: 'comments', value: num(r.comments) }
        ];
    }
    get indexLabel() { return this.reel.index !== null && this.reel.index !== undefined ? this.reel.index + '× a normal Reel' : null; }
    get holdReason() { return this.reel.status === 'Rendered' && this.reel.approval === 'Pending' ? this.reel.holdReason : null; }
}

function ring(score) {
    const n = Math.max(0, Math.min(10, Number(score) || 0));
    const color = n >= 8 ? '#3ddc97' : n >= 7 ? '#ffc24b' : '#ff5c5c';
    return `background:conic-gradient(${color} ${n * 36}deg, rgba(255,255,255,0.09) 0deg)`;
}
