import { LightningElement } from 'lwc';
import listComments from '@salesforce/apex/AdminPanelController.listComments';
import { errorMessage, formatIst } from 'c/instaUtils';

const TONES = { Posted: 'badge badge_good', Drafted: 'badge badge_info', Skipped: 'badge badge_muted', Failed: 'badge badge_bad' };

export default class InstaComments extends LightningElement {
    rows = [];
    loading = true;
    error;

    async connectedCallback() {
        try {
            const data = await listComments({ limitSize: 100 });
            this.rows = data.map((c) => ({
                ...c,
                badge: TONES[c.Status__c] || 'badge badge_muted',
                when: formatIst(c.Posted_At__c || c.CreatedDate),
                reel: c.Social_Post__r && c.Social_Post__r.Content__r ? c.Social_Post__r.Content__r.Name : '',
                link: c.Social_Post__r ? c.Social_Post__r.Permalink__c : null,
                isInsult: c.Category__c === 'insult'
            }));
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.loading = false;
        }
    }

    get hasRows() {
        return this.rows.length > 0;
    }
}
