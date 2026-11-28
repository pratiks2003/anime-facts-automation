import { LightningElement, api } from 'lwc';
import { dayClock, indexBar, num, statusClass, verdictClass } from 'c/mcUtils';

const FILTERS = [
    { id: 'all', label: 'All' },
    { id: 'posted', label: 'Posted' },
    { id: 'winners', label: 'Winners' },
    { id: 'working', label: 'In progress' },
    { id: 'failed', label: 'Failed' }
];

export default class McReels extends LightningElement {
    @api overview;
    @api loading;
    filter = 'all';

    get ready() { return !!this.overview; }

    get filters() {
        return FILTERS.map((f) => ({ ...f, cls: f.id === this.filter ? 'chip on' : 'chip' }));
    }
    pick(event) {
        this.filter = event.currentTarget.dataset.id;
    }

    get reels() {
        const rows = (this.overview && this.overview.reels) || [];
        return rows
            .filter((r) => {
                if (this.filter === 'posted') return r.status === 'Published';
                if (this.filter === 'winners') return r.verdict === 'Winner';
                if (this.filter === 'failed') return r.status === 'Failed';
                if (this.filter === 'working') return ['Queued', 'Generating', 'Rendered', 'Approved', 'Publishing'].includes(r.status);
                return true;
            })
            .map((r) => {
                const bar = indexBar(r.index);
                return {
                    ...r,
                    title: r.show || r.format || r.id,
                    when: dayClock(r.publishedAt || r.scheduledFor || r.createdDate),
                    statusCls: statusClass(r.status),
                    verdictCls: verdictClass(r.verdict),
                    indexLabel: r.index !== null && r.index !== undefined ? r.index + '×' : null,
                    barCls: bar.cls,
                    barStyle: bar.width,
                    stats: [
                        { k: 'v', label: 'views', value: num(r.views) },
                        { k: 'w', label: 'watch', value: r.watchSeconds ? r.watchSeconds + 's' : '–' },
                        { k: 's', label: 'shares', value: num(r.shares) },
                        { k: 'q', label: 'edit', value: r.editScore ? r.editScore + '/10' : '–' },
                        { k: 'd', label: 'on brief', value: r.directionScore ? r.directionScore + '/10' : '–' }
                    ],
                    effectChips: (r.effects || []).slice(0, 6).map((e, i) => ({ key: r.id + i, label: String(e).replace(/_/g, ' ') })),
                    hasEffects: (r.effects || []).length > 0
                };
            });
    }
    get hasReels() { return this.reels.length > 0; }

    open(event) {
        this.dispatchEvent(new CustomEvent('openreel', { detail: { id: event.currentTarget.dataset.id } }));
    }
    stop(event) {
        event.stopPropagation();
    }
}
