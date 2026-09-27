// Jarvis voice helpers under Node: sentence streaming and echo detection.
// Run: node tests/voice.test.mjs (tests/test_frontend_intents.py runs it too)
globalThis.window = {};                       // no speech APIs here: the speaker collects text
const V = await import("../app/q/js/jarvis/voice.js");

let fail = 0;
const check = (cond, msg) => { if (!cond) { fail++; console.error("FAIL  " + msg); } };

// sentences are released as soon as they complete, never mid-number
{
  const sp = V.createSpeaker({ enabled: true });
  sp.push("Two approvals are pend");
  check(sp.spoken.trim() === "", "nothing spoken before a sentence ends");
  sp.push("ing. The redesign is 3.");
  check(sp.spoken.trim() === "Two approvals are pending.", `first sentence released (got "${sp.spoken.trim()}")`);
  sp.push("5 weeks late! Want me to open it?");
  check(sp.spoken.includes("3.5 weeks late!"), "decimals are not split");
  check(!sp.spoken.includes("open it?"), "unfinished final sentence waits for end()");
  sp.end();
  check(sp.spoken.includes("Want me to open it?"), "end() flushes the tail");
  let resolved = false;
  await sp.done.then(() => { resolved = true; });
  check(resolved, "done resolves once everything is spoken");
}

// stop() silences everything that follows
{
  const sp = V.createSpeaker({});
  sp.push("First. ");
  sp.stop();
  sp.push("Second sentence. ");
  sp.end();
  check(!sp.spoken.includes("Second"), "nothing is queued after stop()");
  check(!sp.active, "a stopped speaker is inactive");
}

// the mic hearing Jarvis is not an interruption; the operator talking is
check(V.isEcho("the redesign is overdue", "The website redesign is overdue by six weeks."), "his own words are echo");
check(!V.isEcho("stop, open approvals instead", "The website redesign is overdue by six weeks."), "new words are an interruption");
check(V.isEcho("", "anything"), "silence is not an interruption");

console.log(`${fail ? "FAILED" : "ok"}: voice checks ${fail ? fail + " failed" : "passed"}`);
process.exit(fail ? 1 : 0);
