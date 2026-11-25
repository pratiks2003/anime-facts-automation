@IsTest
private class CreativeStrategistTest {
    static Map<String, Object> request(String cid) {
        return new Map<String, Object>{
            'content_id' => cid,
            'slot' => 'Morning',
            'scheduled_format' => 'Fact',
            'formats' => new List<Object>{
                new Map<String, Object>{ 'name' => 'Fact', 'about' => 'one surprising fact' },
                new Map<String, Object>{ 'name' => 'Backstory', 'about' => 'origin story' }
            }
        };
    }

    @IsTest
    static void exploitPicksNamedStyleAndRecordsStrategy() {
        insert new Anime_Content__c(Name = 'T', External_Id__c = 'S-1', Format__c = 'Fact', Status__c = 'Generating');
        CreativeStrategist.roll = 0.9; // exploit
        PromptRunner.mock = new Map<String, List<String>>{
            CreativeStrategist.TEMPLATE => new List<String>{
                '{"format":"Backstory","angle":"the tragedy nobody noticed","style_name":"Cinematic Story","length_seconds":50,"reasoning":"stories retain"}'
            }
        };
        Test.startTest();
        Map<String, Object> d = CreativeStrategist.decide(request('S-1'));
        Test.stopTest();
        Assert.areEqual('Backstory', d.get('format'));
        Assert.areEqual('exploit', d.get('mode'));
        Assert.areEqual('Cinematic Story', d.get('style_name'));
        Assert.areEqual('cinematic', ((Map<String, Object>) d.get('style')).get('layout'));
        Anime_Content__c c = [SELECT Style__r.Name, Strategy__c FROM Anime_Content__c WHERE External_Id__c = 'S-1'];
        Assert.areEqual('Cinematic Story', c.Style__r.Name);
        Assert.isTrue(c.Strategy__c.contains('tragedy'));
        Assert.areEqual(4, [SELECT COUNT() FROM Style__c], 'seeds created once');
    }

    @IsTest
    static void unknownFormatFallsBackAndTestModeForcesTestingStyle() {
        CreativeStrategist.roll = 0.1; // test
        PromptRunner.mock = new Map<String, List<String>>{
            CreativeStrategist.TEMPLATE => new List<String>{ '{"format":"Dance","style_name":"Cinematic Story"}' }
        };
        Map<String, Object> d = CreativeStrategist.decide(request(null));
        Assert.areEqual('Fact', d.get('format'));
        Assert.areEqual('test', d.get('mode'));
        Style__c s = [SELECT Status__c FROM Style__c WHERE Name = :(String) d.get('style_name')];
        Assert.areEqual('Testing', s.Status__c);
    }

    @IsTest
    static void inventCreatesTestingStyle() {
        CreativeStrategist.roll = 0.4; // invent
        PromptRunner.mock = new Map<String, List<String>>{
            CreativeStrategist.TEMPLATE => new List<String>{
                '{"format":"Fact","new_style":{"name":"Manga Panel Rush","why":"manga panels trend","spec":{"layout":"cinematic","image_interval":1.8,"grading":"none"}}}'
            }
        };
        Map<String, Object> d = CreativeStrategist.decide(request(null));
        Assert.areEqual('Manga Panel Rush', d.get('style_name'));
        Style__c s = [SELECT Status__c, Origin__c, Spec__c FROM Style__c WHERE Name = 'Manga Panel Rush'];
        Assert.areEqual('Invented', s.Origin__c);
        Assert.areEqual('Testing', s.Status__c);
        Assert.isTrue(s.Spec__c.contains('1.8'));
    }

    @IsTest
    static void statsGraduateOrRetireTestingStyles() {
        StyleLibrary.ensureSeeds();
        Style__c good = [SELECT Id FROM Style__c WHERE Name = 'Hype Montage'];
        Style__c bad = [SELECT Id FROM Style__c WHERE Name = 'Lore Slow-Burn'];
        List<Anime_Content__c> reels = new List<Anime_Content__c>();
        for (Integer i = 0; i < 6; i++) {
            reels.add(new Anime_Content__c(Name = 'R' + i, External_Id__c = 'X-' + i, Format__c = 'Fact', Status__c = 'Published',
                Style__c = i < 3 ? good.Id : bad.Id));
        }
        insert reels;
        List<Social_Post__c> posts = new List<Social_Post__c>();
        for (Integer i = 0; i < 6; i++) {
            posts.add(new Social_Post__c(Content__c = reels[i].Id, Instagram_Media_Id__c = 'M' + i, Status__c = 'Published',
                Views__c = i < 3 ? 900 : 100, Avg_Watch_Time_Ms__c = 7000, Shares__c = 2));
        }
        insert posts;
        StyleLibrary.refreshStats();
        Assert.areEqual('Active', [SELECT Status__c FROM Style__c WHERE Id = :good.Id].Status__c);
        Assert.areEqual('Retired', [SELECT Status__c FROM Style__c WHERE Id = :bad.Id].Status__c);
        Assert.areEqual(1.8, [SELECT Performance_Score__c FROM Style__c WHERE Id = :good.Id].Performance_Score__c);
    }

    @IsTest
    static void restEndpointReturnsDecisionOrError() {
        CreativeStrategist.roll = 0.9;
        PromptRunner.mock = new Map<String, List<String>>{
            CreativeStrategist.TEMPLATE => new List<String>{ '{"format":"Fact","style_name":"Cinematic Story"}' }
        };
        RestContext.request = new RestRequest();
        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf(JSON.serialize(request(null)));
        StrategyResource.run();
        Assert.areEqual(200, RestContext.response.statusCode);
        StrategyResource.run(); // no mock left -> 500 with an error body
        Assert.areEqual(500, RestContext.response.statusCode);
    }

    @IsTest
    static void formatIsDynamicButNeverRepeatsTheLastTwo() {
        Map<String, Object> req = request('S-9');
        Assert.areEqual('Backstory', CreativeStrategist.validFormat('Backstory', req), 'any allowed format');
        Assert.areEqual('Fact', CreativeStrategist.validFormat('Dance', req), 'unknown -> rotation');
        insert new Anime_Content__c(Name = 'R', External_Id__c = 'P-1', Format__c = 'Backstory', Status__c = 'Published');
        Assert.areEqual('Fact', CreativeStrategist.validFormat('Backstory', req), 'repeat of a recent format -> rotation');
    }
}
