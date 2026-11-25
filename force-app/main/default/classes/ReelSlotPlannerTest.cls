@IsTest
private class ReelSlotPlannerTest {
    @IsTest
    static void istConversion() {
        Datetime at = ReelSlotPlanner.istToGmt(Date.newInstance(2026, 11, 25), '09:00');
        Assert.areEqual(Datetime.newInstanceGmt(2026, 11, 25, 3, 30, 0), at);
    }

    @IsTest
    static void morningOnlyWhenTwoPerDayOff() {
        InstaAgent_Settings__c s = InstaAgentTestUtil.settings(true, false);
        // 08:40 IST -> morning slot (09:00) is within the 60 minute lead window
        Datetime now = InstaAgentTestUtil.morningSlotGmt().addMinutes(-20);
        List<ReelSlotPlanner.Slot> due = ReelSlotPlanner.dueSlots(now, s);
        Assert.areEqual(1, due.size());
        Assert.areEqual('R-20261125-AM', due[0].externalId);
        Assert.areEqual('Wed', due[0].dayOfWeek);
    }

    @IsTest
    static void eveningSlotOnlyWhenEnabled() {
        Datetime evening = ReelSlotPlanner.istToGmt(Date.newInstance(2026, 11, 25), '19:00').addMinutes(-20);
        Assert.areEqual(0, ReelSlotPlanner.dueSlots(evening, InstaAgentTestUtil.settings(true, false)).size());
        List<ReelSlotPlanner.Slot> due = ReelSlotPlanner.dueSlots(evening, InstaAgentTestUtil.settings(true, true));
        Assert.areEqual(1, due.size());
        Assert.areEqual('R-20261125-PM', due[0].externalId);
    }

    @IsTest
    static void nothingDueFarFromSlot() {
        Datetime midday = ReelSlotPlanner.istToGmt(Date.newInstance(2026, 11, 25), '14:00');
        Assert.areEqual(0, ReelSlotPlanner.dueSlots(midday, InstaAgentTestUtil.settings(true, true)).size());
    }

    @IsTest
    static void planDueIsIdempotentAndUsesRotation() {
        InstaAgentTestUtil.settings(true, false);
        Datetime now = InstaAgentTestUtil.morningSlotGmt().addMinutes(-20);
        List<Anime_Content__c> first = ReelSlotPlanner.planDue(now);
        List<Anime_Content__c> second = ReelSlotPlanner.planDue(now);
        Assert.areEqual(1, first.size());
        Assert.areEqual(0, second.size(), 'second run must not duplicate the slot');
        Anime_Content__c c = [SELECT Format__c, Status__c, Slot__c, Dry_Run__c FROM Anime_Content__c];
        Assert.areEqual(ReelSlotPlanner.rotation().get('Wed|Morning'), c.Format__c);
        Assert.areEqual('Queued', c.Status__c);
        Assert.areEqual('Morning', c.Slot__c);
        Assert.isTrue(c.Dry_Run__c);
    }

    @IsTest
    static void rotationCoversEveryDayAndSlot() {
        Map<String, String> rotation = ReelSlotPlanner.rotation();
        for (String day : new List<String>{ 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun' }) {
            Assert.isNotNull(rotation.get(day + '|Morning'), day + ' morning');
            Assert.isNotNull(rotation.get(day + '|Evening'), day + ' evening');
        }
    }

    @IsTest
    static void invalidTimeRejected() {
        try {
            InstaAgentConfig.validTime('9am');
            Assert.fail('expected exception');
        } catch (InstaAgentConfig.InstaAgentException e) {
            Assert.isTrue(e.getMessage().contains('HH:mm'));
        }
    }

    @IsTest
    static void threeReelsPerDayAddsMidday() {
        InstaAgent_Settings__c s = InstaAgentTestUtil.settings(true, true);
        s.Three_Reels_Per_Day__c = true;
        s.Midday_Slot_IST__c = '13:30';
        Map<String, String> slots = ReelSlotPlanner.enabledSlots(s);
        Assert.areEqual(new List<String>{ 'Morning', 'Midday', 'Evening' }, new List<String>(slots.keySet()));
        // 13:30 IST = 08:00 GMT; at 07:30 GMT the Midday slot is due with its own id
        Datetime now = Datetime.newInstanceGmt(2026, 11, 26, 7, 30, 0);
        Boolean found = false;
        for (ReelSlotPlanner.Slot slot : ReelSlotPlanner.dueSlots(now, s)) {
            found = found || (slot.name == 'Midday' && slot.externalId == 'R-20261126-MD');
        }
        Assert.isTrue(found, 'midday slot due');
        s.Two_Reels_Per_Day__c = false;
        Assert.areEqual(1, ReelSlotPlanner.enabledSlots(s).size(), 'three needs two');
    }
}
