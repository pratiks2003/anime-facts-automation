import { LightningElement } from 'lwc';
import listReels from '@salesforce/apex/AdminPanelController.listReels';
import approveReel from '@salesforce/apex/AdminPanelController.approveReel';
import rejectReel from '@salesforce/apex/AdminPanelController.rejectReel';
import retryReel from '@salesforce/apex/AdminPanelController.retryReel';
import { formatIst, statusClass, errorMessage } from 'c/instaUtils';

const FILTERS = ['All', 'Rendered', 'Generating', 'Published', 'Failed', 'Queued'];
const RETRYABLE = new Set(['Failed', 'Rejected', 'Cancelled', 'Planned']);

export default class InstaReels extends LightningElement {
    filter = 'All';
    reels = [];
    selectedId;
    loading = true;
    busyId;
    error;

    connectedCallback() {
        this.load();
    }

    async load() {
        this.loading = true;
        try {
            this.reels = await listReels({ status: this.filter === 'All' ? null : this.filter, limitSize: 60 });
            this.error = undefined;
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.loading = false;
        }
    }

    get filters() {
        return FILTERS.map((f) => ({ value: f, className: f === this.filter ? 'pill pill_active' : 'pill' }));
    }

    get cards() {
        return this.reels.map((r) => {
            const post = r.Social_Posts__r && r.Social_Posts__r.length ? r.Social_Posts__r[0] : null;
            return {
                ...r,
                anime: r.Anime__r ? r.Anime__r.Name : '',
                badge: statusClass(r.Status__c),
                when: formatIst(r.Scheduled_For__c),
                hasCover: !!r.Cover_URL__c,
                canReview: r.Status__c === 'Rendered',
                canRetry: RETRYABLE.has(r.Status__c),
                permalink: post ? post.Permalink__c : null,
                busy: this.busyId === r.Id,
                captionPreview: r.Caption__c ? r.Caption__c.split('\n')[0] : r.Topic__c || '',
                hasScore: r.Quality_Score__c !== undefined && r.Quality_Score__c !== null,
                scoreClass: r.Quality_Score__c >= 8.5 ? 'score score_good' : r.Quality_Score__c >= 7 ? 'score score_ok' : 'score score_bad',
                held: r.Status__c === 'Rendered' && r.Approval_Status__c === 'Pending',
                brief: this.briefText(r.Production_Brief__c)
            };
        });
    }

    briefText(json) {
        if (!json) {
            return '';
        }
        try {
            const b = JSON.parse(json);
            return [b.voice && `voice ${b.voice}`, b.speed && `${b.speed}x`, b.music_mood && `${b.music_mood} music`, b.music_energy && `${b.music_energy} energy`, b.sfx && `${b.sfx} sfx`]
                .filter(Boolean)
                .join(' · ');
        } catch (e) {
            return '';
        }
    }

    get hasCards() {
        return this.cards.length > 0;
    }

    get selected() {
        return this.cards.find((c) => c.Id === this.selectedId);
    }

    handleFilter(event) {
        this.filter = event.currentTarget.dataset.value;
        this.load();
    }

    handleOpen(event) {
        this.selectedId = event.currentTarget.dataset.id;
    }

    handleClose() {
        this.selectedId = undefined;
    }

    stop(event) {
        event.stopPropagation();
    }

    async act(event, action) {
        event.stopPropagation();
        const id = event.currentTarget.dataset.id;
        this.busyId = id;
        this.error = undefined;
        try {
            await action({ reelId: id });
            await this.load();
            this.dispatchEvent(new CustomEvent('changed'));
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.busyId = undefined;
        }
    }

    handleApprove(event) {
        this.act(event, approveReel);
    }

    handleReject(event) {
        this.act(event, rejectReel);
    }

    handleRetry(event) {
        this.act(event, retryReel);
    }
}
