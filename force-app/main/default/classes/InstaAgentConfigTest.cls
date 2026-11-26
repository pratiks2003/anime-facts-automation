@IsTest
private class InstaAgentConfigTest {
    @IsTest
    static void defaultsWithoutSettingsRecord() {
        InstaAgentConfig.cached = null;
        InstaAgent_Settings__c s = InstaAgentConfig.get();
        Assert.areEqual('09:00', s.Morning_Slot_IST__c);
        Assert.areEqual('13:30', s.Midday_Slot_IST__c);
        Assert.areEqual(6, s.Daily_Post_Limit__c);
        Map<String, Object> policy = InstaAgentConfig.postingPolicy();
        Assert.areEqual(true, policy.get('enabled'));
        Assert.areEqual(0, policy.get('posts_last_24h'));
        Assert.areEqual(90, policy.get('min_gap_minutes'));
    }

    @IsTest
    static void saveClampsAndValidates() {
        InstaAgentTestUtil.settings(true, true);
        InstaAgentConfig.save(new InstaAgent_Settings__c(Automation_Enabled__c = true, Two_Reels_Per_Day__c = true,
            Three_Reels_Per_Day__c = true, Auto_Approve__c = true, Dry_Run__c = false, Midday_Slot_IST__c = '12:15',
            Post_Limit_Enabled__c = true, Daily_Post_Limit__c = 0, Min_Post_Gap_Minutes__c = 5000));
        InstaAgent_Settings__c s = InstaAgentConfig.get();
        Assert.areEqual(1, s.Daily_Post_Limit__c);
        Assert.areEqual(720, s.Min_Post_Gap_Minutes__c);
        Assert.areEqual('12:15', s.Midday_Slot_IST__c);
        Assert.isTrue(s.Three_Reels_Per_Day__c);
        try {
            InstaAgentConfig.validTime('25:99');
            Assert.fail('bad time accepted');
        } catch (Exception e) {
            Assert.isNotNull(e.getMessage());
        }
    }
}
