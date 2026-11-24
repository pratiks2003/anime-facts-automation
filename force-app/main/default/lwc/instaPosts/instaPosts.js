import { LightningElement } from 'lwc';
import listPosts from '@salesforce/apex/AdminPanelController.listPosts';
import { formatIst, compactNumber, errorMessage, instagramEmbedUrl } from 'c/instaUtils';

export default class InstaPosts extends LightningElement {
    posts = [];
    loading = true;
    error;

    async connectedCallback() {
        try {
            this.posts = await listPosts({ limitSize: 30 });
        } catch (e) {
            this.error = errorMessage(e);
        } finally {
            this.loading = false;
        }
    }

    get items() {
        return this.posts.map((p) => ({
            ...p,
            title: p.Content__r ? p.Content__r.Name : p.Name,
            format: p.Content__r ? p.Content__r.Format__c : '',
            embedUrl: instagramEmbedUrl(p.Permalink__c),
            when: formatIst(p.Published_At__c),
            metrics: [
                { key: 'v', label: 'Views', value: compactNumber(p.Views__c) },
                { key: 'l', label: 'Likes', value: compactNumber(p.Likes__c) },
                { key: 'c', label: 'Comments', value: compactNumber(p.Comments__c) },
                { key: 's', label: 'Shares', value: compactNumber(p.Shares__c) },
                { key: 'sv', label: 'Saves', value: compactNumber(p.Saves__c) },
                {
                    key: 'w',
                    label: 'Avg watch',
                    value: p.Avg_Watch_Time_Ms__c ? `${(p.Avg_Watch_Time_Ms__c / 1000).toFixed(1)}s` : '—'
                }
            ],
            updated: p.Metrics_Updated_At__c ? `Metrics ${formatIst(p.Metrics_Updated_At__c)}` : 'Metrics pending'
        }));
    }

    get hasItems() {
        return this.items.length > 0;
    }
}
