import { LightningElement } from 'lwc';
import getIdeas from '@salesforce/apex/AdminPanelController.getIdeas';
import setIdeaStatus from '@salesforce/apex/AdminPanelController.setIdeaStatus';
import { errorMessage, formatIst } from 'c/instaUtils';

export default class InstaIdeas extends LightningElement {
    ideas = [];
    review;
    loading = true;
    error;

    connectedCallback() {
        this.load();
    }

    async load() {
        try {
            const data = await getIdeas();
            this.ideas = data.ideas.map((i) => ({
                ...i,
                suggested: i.Status__c === 'Suggested',
                badge: i.Status__c === 'Suggested' ? 'badge badge_warn' : 'badge badge_good',
                when: formatIst(i.CreatedDate)
            }));
            this.review = data.review && data.review.length ? data.review[0] : null;
            this.error = undefined;
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.loading = false;
        }
    }

    get hasIdeas() {
        return this.ideas.length > 0;
    }

    get lessons() {
        return this.review && this.review.Hook_Lessons__c ? this.review.Hook_Lessons__c.split('\n').filter((l) => l.trim()) : [];
    }

    async setStatus(event, status) {
        try {
            await setIdeaStatus({ ideaId: event.currentTarget.dataset.id, status });
            await this.load();
        } catch (e) {
            this.error = errorMessage(e);
        }
    }

    handleApprove(event) {
        this.setStatus(event, 'New');
    }

    handleReject(event) {
        this.setStatus(event, 'Rejected');
    }
}
