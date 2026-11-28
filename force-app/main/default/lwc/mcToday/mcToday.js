import { LightningElement, api } from 'lwc';
import resolveItem from '@salesforce/apex/MissionControlController.resolveItem';
import { ago, clock, errorText, num, verdictClass, agentStyle } from 'c/mcUtils';

const STEPS = ['Plan', 'Create', 'Check', 'Post', 'Live'];

export default class McToday extends LightningElement {
    @api overview;
    @api loading;
    now = Date.now();
    tick;
    resolving = {};
    message;

    connectedCallback() {
        this.tick = setInterval(() => { this.now = Date.now(); }, 1000); // eslint-disable-line @lwc/lwc/no-async-operation
    }
    disconnectedCallback() {
        clearInterval(this.tick);
    }

    get ready() { return !!this.overview; }
    get ap() { return (this.overview && this.overview.autopilot) || {}; }
    get kpis() { return (this.overview && this.overview.kpis) || {}; }

    get nextName() { return this.ap.nextSlot || '—'; }
    get nextAt() { return this.ap.nextSlotAt ? clock(this.ap.nextSlotAt) + ' IST' : ''; }
    get countdown() {
        if (!this.ap.nextSlotAt) return '--:--:--';
        let s = Math.max(0, Math.floor((new Date(this.ap.nextSlotAt).getTime() - this.now) / 1000));
        const h = Math.floor(s / 3600); s -= h * 3600;
        const m = Math.floor(s / 60); s -= m * 60;
        const pad = (x) => String(x).padStart(2, '0');
        return `${pad(h)}:${pad(m)}:${pad(s)}`;
    }
    get statusLine() {
        if (!this.ap.automationEnabled) return 'Automation is paused - nothing will be created.';
        if (this.ap.dryRun) return 'Dry run: Reels are made but not posted.';
        return `${this.ap.reelsPerDay || 3} Reels a day, planned and posted by the agents.`;
    }

    get active() { return this.ap.active; }
    get activeSteps() {
        const step = this.active ? this.active.step || 0 : 0;
        return STEPS.map((label, i) => ({ key: label, label, cls: i < step ? 'st done' : i === step ? 'st now' : 'st' }));
    }
    get activeTitle() {
        const a = this.active;
        if (!a) return '';
        return a.show ? `${a.show} · ${a.format || ''}` : a.format || a.id;
    }

    get inbox() {
        const rows = (this.overview && this.overview.inbox) || [];
        return rows.map((r) => {
            const a = agentStyle(r.agent);
            return { ...r, when: ago(r.createdDate), avatar: a.letter, avatarStyle: a.style,
                sevCls: r.severity === 'critical' ? 'pill bad' : r.severity === 'warning' ? 'pill warn' : 'pill',
                busy: !!this.resolving[r.id] };
        });
    }
    get hasInbox() { return this.inbox.length > 0; }

    get slots() {
        const sched = this.ap.schedule || [];
        const reels = (this.overview && this.overview.reels) || [];
        const todayKey = this.istDate(new Date());
        return sched.map((s) => {
            // a slot's Reel has a fixed id: R-<yyyyMMdd IST>-AM|MD|PM (test / dry-run Reels never take its place)
            const slotId = 'R-' + todayKey.replace(/-/g, '') + '-' + ({ Morning: 'AM', Midday: 'MD', Evening: 'PM' }[s.name] || '');
            const reel = reels.find((r) => r.id === slotId);
            let state = s.done ? 'Missed' : 'Planned';
            let cls = 'pill';
            if (reel) {
                if (reel.status === 'Published') { state = 'Posted'; cls = 'pill good'; }
                else if (reel.status === 'Failed') { state = 'Failed'; cls = 'pill bad'; }
                else { state = 'Creating'; cls = 'pill live'; }
            } else if (!s.done) {
                state = 'Upcoming';
            }
            return { key: s.name, name: s.name, time: s.time, state, cls, reel,
                show: reel ? reel.show : null, topic: reel ? reel.topic : null,
                verdict: reel ? reel.verdict : null, verdictCls: reel ? verdictClass(reel.verdict) : 'pill',
                views: reel && reel.views !== null && reel.views !== undefined ? num(reel.views) + ' views' : null,
                cover: reel ? reel.cover : null, rid: reel ? reel.id : null };
        });
    }

    istDate(d) {
        try {
            return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(d);
        } catch (e) {
            return d.toISOString().slice(0, 10);
        }
    }

    get kpiCards() {
        const k = this.kpis;
        return [
            { key: 'v', label: 'Views · 7 days', value: num(k.views7d) },
            { key: 'w', label: 'Avg watch', value: k.avgWatchSeconds ? k.avgWatchSeconds + 's' : '–' },
            { key: 'r', label: 'Winners / losers', value: `${k.winners || 0} / ${k.losers || 0}` },
            { key: 'i', label: 'Avg performance', value: k.avgIndex ? k.avgIndex + '×' : '–' }
        ];
    }
    get postingLine() {
        const k = this.kpis;
        return k.posts24h !== undefined ? `${k.posts24h} of ${k.maxPerDay} posts in the last 24 h` : '';
    }

    openActive() {
        if (this.active) this.dispatchEvent(new CustomEvent('openreel', { detail: { id: this.active.id } }));
    }
    openSlot(event) {
        const id = event.currentTarget.dataset.id;
        if (id) this.dispatchEvent(new CustomEvent('openreel', { detail: { id } }));
    }
    openInboxReel(event) {
        const id = event.currentTarget.dataset.id;
        if (id) this.dispatchEvent(new CustomEvent('openreel', { detail: { id } }));
    }

    async resolve(event) {
        event.stopPropagation();
        const id = event.currentTarget.dataset.id;
        this.resolving = { ...this.resolving, [id]: true };
        try {
            await resolveItem({ activityId: id });
            this.message = undefined;
            this.dispatchEvent(new CustomEvent('refresh'));
        } catch (e) {
            this.message = errorText(e);
        } finally {
            this.resolving = { ...this.resolving, [id]: false };
        }
    }
}
