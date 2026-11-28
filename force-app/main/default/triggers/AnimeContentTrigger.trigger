trigger AnimeContentTrigger on Anime_Content__c(after update) {
    ReelTriggerHandler.afterUpdate(Trigger.new, Trigger.oldMap);
    ShowrunnerEvents.afterUpdate(Trigger.new, Trigger.oldMap);
}
