@IsTest
private class AdminPanelControllerTest {
    @IsTest
    static void overviewAndSettingsRoundTrip() {
        System.runAs(InstaAgentTestUtil.adminUser()) {
            InstaAgentTestUtil.settings(false, false);
            Map<String, Object> overview = AdminPanelController.getOverview();
            Map<String, Object> settings = (Map<String, Object>) overview.get('settings');
            Assert.areEqual(false, settings.get('twoReelsPerDay'));
            Assert.isFalse(((List<Object>) overview.get('upcoming')).isEmpty());

            Test.startTest();
            Map<String, Object> saved = AdminPanelController.saveSettings(
                new Map<String, Object>{
                    'automationEnabled' => true,
                    'twoReelsPerDay' => true,
                    'autoApprove' => true,
                    'dryRun' => false,
                    'morningSlot' => '08:30',
                    'eveningSlot' => '20:00'
                }
            );
            Test.stopTest();
            Assert.areEqual(true, saved.get('twoReelsPerDay'));
            Assert.areEqual('08:30', saved.get('morningSlot'));
            Assert.areEqual(1, [SELECT COUNT() FROM CronTrigger WHERE CronJobDetail.Name = :ReelScheduler.JOB_NAME], 'enabling automation schedules the heartbeat');
        }
    }

    @IsTest
    static void invalidSlotTimeSurfacesError() {
        System.runAs(InstaAgentTestUtil.adminUser()) {
            InstaAgentTestUtil.settings(false, false);
            try {
                AdminPanelController.saveSettings(
                    new Map<String, Object>{
                        'automationEnabled' => false,
                        'twoReelsPerDay' => false,
                        'autoApprove' => true,
                        'dryRun' => true,
                        'morningSlot' => '25:00',
                        'eveningSlot' => '19:00'
                    }
                );
                Assert.fail('expected AuraHandledException');
            } catch (AuraHandledException e) {
                Assert.isTrue(e.getMessage().contains('HH:mm'));
            }
        }
    }

    @IsTest
    static void upcomingSlotsRespectTwoPerDay() {
        System.runAs(InstaAgentTestUtil.adminUser()) {
            InstaAgent_Settings__c s = InstaAgentTestUtil.settings(true, true);
            List<Map<String, Object>> slots = AdminPanelController.upcomingSlots(InstaAgentTestUtil.morningSlotGmt().addHours(-2), s, 4);
            Assert.areEqual(4, slots.size());
            Assert.areEqual('Morning', slots[0].get('slot'));
            Assert.areEqual('Evening', slots[1].get('slot'));
        }
    }

    @IsTest
    static void createRetryRejectFlows() {
        System.runAs(InstaAgentTestUtil.adminUser()) {
            InstaAgentTestUtil.settings(true, false);
            Test.setMock(HttpCalloutMock.class, new InstaAgentTestUtil.GitHubMock(204));
            Test.startTest();
            Id manual = AdminPanelController.createReelNow('Quiz', 'Guess the Straw Hat');
            Test.stopTest();
            Anime_Content__c c = [SELECT Format__c, Topic__c, Slot__c, Status__c FROM Anime_Content__c WHERE Id = :manual];
            Assert.areEqual('Quiz', c.Format__c);
            Assert.areEqual('Manual', c.Slot__c);
            Assert.areEqual('Generating', c.Status__c);

            Anime_Content__c rendered = new Anime_Content__c(Name = 'R', External_Id__c = 'R-X', Status__c = 'Rendered', Format__c = 'Fact');
            insert rendered;
            AdminPanelController.rejectReel(rendered.Id);
            Assert.areEqual('Rejected', [SELECT Status__c FROM Anime_Content__c WHERE Id = :rendered.Id].Status__c);

            try {
                AdminPanelController.approveReel(rendered.Id); // no longer Rendered
                Assert.fail('expected error');
            } catch (AuraHandledException e) {
                Assert.isTrue(e.getMessage().contains('rendered'));
            }
            AdminPanelController.retryReel(rendered.Id);
            Assert.areEqual('Queued', [SELECT Status__c FROM Anime_Content__c WHERE Id = :rendered.Id].Status__c);
            Assert.isFalse(AdminPanelController.getFormats().isEmpty());
            Assert.isNotNull(AdminPanelController.listPosts(5));
        }
    }

    @IsTest
    static void ideasModeration() {
        System.runAs(InstaAgentTestUtil.adminUser()) {
            Topic_Idea__c idea = new Topic_Idea__c(Name = 'Zoro gets lost', Status__c = 'Suggested', Source__c = 'Public Vote');
            insert idea;
            insert new Performance_Review__c(Summary__c = 'ok', Period_End__c = Date.today());
            Map<String, Object> data = AdminPanelController.getIdeas();
            Assert.areEqual(1, ((List<Object>) data.get('ideas')).size());
            Assert.areEqual(1, ((List<Object>) data.get('review')).size());
            AdminPanelController.setIdeaStatus(idea.Id, 'New');
            Assert.areEqual('New', [SELECT Status__c FROM Topic_Idea__c].Status__c);
            try {
                AdminPanelController.setIdeaStatus(idea.Id, 'Used');
                Assert.fail('only New/Rejected allowed');
            } catch (AuraHandledException e) {
                Assert.isTrue(e.getMessage().contains('New or Rejected'));
            }
        }
    }
}
