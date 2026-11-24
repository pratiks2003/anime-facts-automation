@IsTest
private class ScriptStudioTest {
    static final String SCRIPT_V1 = '{"title":"Drops","mood":"hype","scenes":[{"role":"hook","headline":"JoJo is back","narration":"v1"},{"role":"outro","headline":"Your pick?","narration":"Which one?"}],"caption":"x","hashtags":["#a"]}';
    static final String SCRIPT_V2 = '{"title":"Drops","mood":"hype","scenes":[{"role":"hook","headline":"JoJo returns Friday","narration":"v2"},{"role":"outro","headline":"Your pick?","narration":"Which one?"}],"caption":"x","hashtags":["#a"]}';
    static final String SCRIPT_V3 = '{"title":"Drops","mood":"hype","scenes":[{"role":"hook","headline":"JoJo returns Friday","narration":"v3"},{"role":"outro","headline":"Your pick?","narration":"Which one?"}],"caption":"x","hashtags":["#a"]}';
    static final String BRIEF = '"production":{"voice":"am_fenrir","speed":1.1,"music_mood":"hype","music_energy":"high","sfx":"punchy","music_keywords":"epic orchestral"}';

    static Map<String, Object> request() {
        return new Map<String, Object>{
            'content_id' => 'R-TEST-1',
            'format' => 'This Week\'s Drops',
            'guide' => new Map<String, Object>{ 'mode' => 'fact', 'shape' => 'hook -> shows -> outro', 'tone' => 'hype' },
            'anime' => 'Anime',
            'facts' => new List<Object>{ new Map<String, Object>{ 'id' => 'NEWS-001', 'content' => 'Episode 10 airs Friday.' } }
        };
    }

    @IsTest
    static void rewritesUntilApprovedThenFixesFactsAndCaptions() {
        insert new Anime_Content__c(Name = 'T', External_Id__c = 'R-TEST-1', Format__c = 'Fact', Status__c = 'Generating');
        PromptRunner.mock = new Map<String, List<String>>{
            ScriptStudio.WRITER => new List<String>{ '```json\n' + SCRIPT_V1 + '\n```' },
            ScriptStudio.DIRECTOR => new List<String>{
                '{"overall":6.2,"verdict":"revise","scores":{"hook":5},"notes":["Hook is generic"],' + BRIEF + ',"revised_script":' + SCRIPT_V2 + '}',
                '{"overall":8.9,"verdict":"approve","scores":{"hook":9},"notes":["Strong"],' + BRIEF + ',"revised_script":null}',
                '{"overall":9.1,"verdict":"revise","scores":{"hook":9},"notes":["Fixed claim"],' + BRIEF + ',"revised_script":' + SCRIPT_V3 + '}'
            },
            ScriptStudio.FACT_CHECKER => new List<String>{
                '{"pass":false,"issues":[{"scene":1,"sentence":"v2","problem":"unsupported","fix":"cite NEWS-001"}]}',
                '{"pass":true,"issues":[]}'
            },
            ScriptStudio.CAPTION => new List<String>{ '{"caption":"Friday is JoJo day 🔥\\nWhich are you watching?","hashtags":["#anime","#jojo","#steelballrun","#animenews","#otaku"]}' }
        };
        Test.startTest();
        ScriptStudio.Result r = ScriptStudio.run(request());
        ScriptStudio.record('R-TEST-1', r);
        Test.stopTest();

        Assert.areEqual('v3', (String) ((Map<String, Object>) ((List<Object>) r.script.get('scenes'))[0]).get('narration'));
        Assert.areEqual(9.1, r.overall);
        Assert.isTrue(r.factsPass);
        Assert.areEqual(7, r.calls, 'writer + 3 director + 2 fact checks + caption');
        Assert.areEqual(5, ((List<Object>) r.script.get('hashtags')).size());
        Assert.areEqual('am_fenrir', r.production.get('voice'));
        Anime_Content__c c = [SELECT Quality_Score__c, Director_Notes__c, Production_Brief__c, Script_Source__c FROM Anime_Content__c];
        Assert.areEqual(9.1, c.Quality_Score__c);
        Assert.areEqual('Agentforce', c.Script_Source__c);
        Assert.isTrue(c.Production_Brief__c.contains('epic orchestral'));
    }

    @IsTest
    static void restEndpointReturnsScriptAndValidatesInput() {
        PromptRunner.mock = new Map<String, List<String>>{
            ScriptStudio.WRITER => new List<String>{ SCRIPT_V1 },
            ScriptStudio.DIRECTOR => new List<String>{ '{"overall":9,"verdict":"approve","scores":{},"notes":[],' + BRIEF + '}' },
            ScriptStudio.FACT_CHECKER => new List<String>{ '{"pass":true,"issues":[]}' },
            ScriptStudio.CAPTION => new List<String>{ '{"caption":"c","hashtags":["#a","#b","#c","#d","#e"]}' }
        };
        RestContext.response = new RestResponse();
        RestContext.request = new RestRequest();
        RestContext.request.requestBody = Blob.valueOf(JSON.serialize(request()));
        ScriptStudioResource.run();
        Assert.areEqual(200, RestContext.response.statusCode);
        Map<String, Object> body = (Map<String, Object>) JSON.deserializeUntyped(RestContext.response.responseBody.toString());
        Assert.areEqual(4, (Integer) body.get('calls'));

        RestContext.response = new RestResponse();
        RestContext.request.requestBody = Blob.valueOf('{"format":"Fact"}');
        ScriptStudioResource.run();
        Assert.areEqual(400, RestContext.response.statusCode);
    }

    @IsTest
    static void agentFailureSurfacesAs500() {
        PromptRunner.mock = new Map<String, List<String>>{ ScriptStudio.WRITER => new List<String>{ 'not json at all' } };
        RestContext.response = new RestResponse();
        RestContext.request = new RestRequest();
        RestContext.request.requestBody = Blob.valueOf(JSON.serialize(request()));
        ScriptStudioResource.run();
        Assert.areEqual(500, RestContext.response.statusCode);
        Assert.isTrue(RestContext.response.responseBody.toString().contains('no JSON object'));
    }
}
