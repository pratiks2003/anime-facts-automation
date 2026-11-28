import { LightningElement, track } from 'lwc';
import getOverview from '@salesforce/apex/MissionControlController.getOverview';
import { errorText } from 'c/mcUtils';

const TABS = [
    { id: 'today', label: 'Today', title: 'Today' },
    { id: 'reels', label: 'Reels', title: 'Reels' },
    { id: 'live', label: 'Live', title: 'Agent brain' },
    { id: 'growth', label: 'Growth', title: 'Growth' },
    { id: 'more', label: 'More', title: 'More' }
];
const REFRESH_MS = 30000;

export default class AdminShell extends LightningElement {
    @track overview;
    tab = 'today';
    reelId;
    error;
    loading = true;
    timer;

    connectedCallback() {
        try {
            const saved = window.sessionStorage.getItem('senpai.tab');
            if (saved && TABS.some((t) => t.id === saved)) this.tab = saved;
        } catch (e) {
            // storage unavailable (private mode) - fine
        }
        this.load();
        this.timer = setInterval(() => this.load(true), REFRESH_MS); // eslint-disable-line @lwc/lwc/no-async-operation
    }

    disconnectedCallback() {
        clearInterval(this.timer);
    }

    async load(silent) {
        if (!silent) this.loading = true;
        try {
            this.overview = await getOverview();
            this.error = undefined;
        } catch (e) {
            this.error = errorText(e);
        } finally {
            this.loading = false;
        }
    }

    go(id) {
        this.tab = id;
        try {
            window.sessionStorage.setItem('senpai.tab', id);
        } catch (e) {
            // ignore
        }
        const main = this.template.querySelector('main');
        if (main && main.scrollTo) main.scrollTo({ top: 0 });
        if (window.scrollTo) window.scrollTo({ top: 0 });
    }

    handleTab(event) {
        this.go(event.currentTarget.dataset.tab);
    }

    handleOpenReel(event) {
        this.reelId = event.detail && event.detail.id;
        this.go('live');
    }

    handleRefresh() {
        this.load(true);
    }

    get tabs() {
        return TABS.map((t) => ({
            ...t,
            cls: t.id === this.tab ? 'tab on' : 'tab',
            current: t.id === this.tab ? 'page' : null,
            isToday: t.id === 'today',
            isReels: t.id === 'reels',
            isLive: t.id === 'live',
            isGrowth: t.id === 'growth',
            isMore: t.id === 'more',
            badge: t.id === 'today' ? this.inboxCount : t.id === 'live' && this.isCreating ? '●' : null
        }));
    }

    get title() {
        const t = TABS.find((x) => x.id === this.tab);
        return t ? t.title : '';
    }

    get autopilot() {
        return (this.overview && this.overview.autopilot) || {};
    }
    get autopilotLabel() {
        if (!this.overview) return '…';
        if (!this.autopilot.automationEnabled) return 'Paused';
        return this.autopilot.dryRun ? 'Dry run' : 'Autopilot';
    }
    get autopilotCls() {
        if (!this.overview) return 'pill';
        if (!this.autopilot.automationEnabled) return 'pill bad';
        return this.autopilot.dryRun ? 'pill warn' : 'pill good';
    }
    get inboxCount() {
        const n = this.overview && this.overview.inbox ? this.overview.inbox.length : 0;
        return n > 0 ? String(n) : null;
    }
    get isCreating() {
        return !!(this.autopilot && this.autopilot.active);
    }

    get isToday() { return this.tab === 'today'; }
    get isReels() { return this.tab === 'reels'; }
    get isLive() { return this.tab === 'live'; }
    get isGrowth() { return this.tab === 'growth'; }
    get isMore() { return this.tab === 'more'; }
}
