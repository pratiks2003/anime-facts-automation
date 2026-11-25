trigger ReferenceReelTrigger on Reference_Reel__c(after insert) {
    ReferenceReels.afterInsert(Trigger.new);
}
