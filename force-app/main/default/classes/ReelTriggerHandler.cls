/**
 * Manual approval path: when a Rendered Reel is approved by a person, start publishing.
 * (Auto-approved Reels are published inside the generate workflow and never hit this.)
 */
public with sharing class ReelTriggerHandler {
    public static void afterUpdate(List<Anime_Content__c> records, Map<Id, Anime_Content__c> oldMap) {
        List<Id> toPublish = new List<Id>();
        for (Anime_Content__c c : records) {
            Anime_Content__c old = oldMap.get(c.Id);
            Boolean newlyApproved = c.Approval_Status__c == 'Approved' && old.Approval_Status__c != 'Approved';
            if (newlyApproved && c.Status__c == 'Rendered') {
                toPublish.add(c.Id);
            }
        }
        if (!toPublish.isEmpty() && Limits.getQueueableJobs() < Limits.getLimitQueueableJobs()) {
            System.enqueueJob(new ReelDispatchQueueable(toPublish, ReelDispatchQueueable.PUBLISH));
        }
    }
}
