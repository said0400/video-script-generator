// remotion/render.mjs
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, symlinkSync } from "fs";
import { spawnSync } from "child_process";
import { tmpdir } from "os";
import { join } from "path";
import { chromium } from "playwright";

const manifestPath = process.argv[2], outputPath = process.argv[3];
if (!manifestPath || !outputPath) { console.error("Usage: node render.mjs <manifest.json> <output.mp4>"); process.exit(1); }

const props = JSON.parse(readFileSync(manifestPath, "utf-8"));
const { title, display_title = title, emoji_left = "🔥", emoji_right = "💥", sentences = [], audio, videos = [], duration_s = 0, power_words = [], aligned = [], lang = "ar", clip_duration = 3.0, clip_durations = [], has_hook = false, custom_hook = "", analysis = {}, mode = "words_only", content_mode = "short" } = props;

const FPS = 30;
const DIMENSIONS = { short: { width: 1080, height: 1920 }, long: { width: 1920, height: 1080 } };
const { width: WIDTH, height: HEIGHT } = DIMENSIONS[content_mode] || DIMENSIONS.short;
const isLong = content_mode === "long", isShort = !isLong;
const TITLE_SLIDE_FRAMES = Math.floor(0.6 * FPS), HOOK_FRAMES = Math.floor(3.0 * FPS), HOOK_INTRO_FRAMES = 0;
const INTRO_FRAMES = Math.floor(1.0 * FPS), OUTRO_FRAMES = Math.floor(1.0 * FPS);
const TRANSITION_DURATION = 0.5, TRANSITION_FRAMES = Math.floor(TRANSITION_DURATION * FPS);
const BROWSER_ARGS = ["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-gpu","--no-zygote","--font-render-hinting=none","--lang=ar,fr,en"];
const XFADE_TRANSITIONS = ["fade","fadeblack","fadegrays","smoothleft","smoothright"];
const PRO_TRANSITION_TYPES = ["slide_burst","zoom_punch","spin_crash","flash_cut","cinema_frame","glitch_wave","liquid_sweep","reveal_drop"];
const TAG_FRAME_COLORS = { shock:"#FF1744",urgency:"#FF6E00",intrigue:"#FFD700",revelation:"#FFFFFF",inspiration:"#00E676",emotional:"#FF4081",confident:"#FFFFFF",wisdom:"#448AFF",calm:"#80DEEA",information:"#FFFFFF",desire:"#FF6EC7",curiosity:"#FFEB3B",storytelling:"#FFA726",dramatic:"#E91E63",tension:"#FF5722",climax:"#FFFFFF",powerful:"#F44336",whisper:"#9C27B0",pause:"#607D8B" };

const safeOut = outputPath.replace(/[^a-zA-Z0-9]/g,"_").replace(/_+/g,"_").slice(-22);
const TMP = join(tmpdir(), `vsg_${safeOut}`); mkdirSync(TMP, { recursive: true });
console.log(`📌 ${emoji_left} ${display_title} ${emoji_right}`);
console.log(`🌐 Lang: ${lang.toUpperCase()} | Mode: ${mode} | Content: ${content_mode.toUpperCase()} | Size: ${WIDTH}×${HEIGHT}`);

const GPS_LOCATIONS = {
  ar:{city:"Riyadh",country:"Saudi Arabia",lat:"24.7136",lon:"46.6753",latRef:"N",lonRef:"E",iso6709:"+24.7136+046.6753/"},
  fr:{city:"Paris",country:"France",lat:"48.8566",lon:"2.3522",latRef:"N",lonRef:"E",iso6709:"+48.8566+002.3522/"},
  en:{city:"New York",country:"United States",lat:"40.7128",lon:"74.0060",latRef:"N",lonRef:"W",iso6709:"+40.7128-074.0060/"}
};
const location = GPS_LOCATIONS[lang] || GPS_LOCATIONS.ar;

function buildiPhoneMetadata() {
  const now=new Date(),dateISO=now.toISOString(),dateStr=dateISO.replace(/[-:]/g,"").split(".")[0];
  const serial="F"+Math.random().toString(36).substring(2,10).toUpperCase();
  const uuid=[Math.random().toString(16).substring(2,10),Math.random().toString(16).substring(2,6),Math.random().toString(16).substring(2,6),Math.random().toString(16).substring(2,6),Math.random().toString(16).substring(2,14)].join("-").toUpperCase();
  const g=()=>(Math.random()*0.02-0.01).toFixed(6), a=()=>(Math.random()*0.1-0.05).toFixed(6);
  return ["-map_metadata","-1","-metadata","make=Apple","-metadata","model=iPhone 17 Pro Max","-metadata","software=Adobe Premiere Pro 25.0","-metadata","encoder=Adobe Premiere Pro 25.0","-metadata","handler_name=Core Media Data Handler","-metadata","com.apple.quicktime.make=Apple","-metadata","com.apple.quicktime.model=iPhone 17 Pro Max","-metadata","com.apple.quicktime.software=iOS 18.2","-metadata",`com.apple.quicktime.creationdate=${dateISO}`,"-metadata",`com.apple.quicktime.location.ISO6709=${location.iso6709}`,"-metadata",`com.apple.quicktime.location.name=${location.city}, ${location.country}`,"-metadata",`com.apple.quicktime.content.identifier=${uuid}`,"-metadata","com.apple.quicktime.fullframerate=1","-metadata",`creation_time=${dateISO}`,"-metadata",`date=${dateStr}`,"-metadata","focal_length=9","-metadata","aperture=f/2.8","-metadata","iso=64","-metadata","exposure_time=1/120","-metadata","white_balance=Auto","-metadata","flash=No Flash","-metadata","lens=Apple iPhone 17 Pro Max back camera 9mm f/2.8","-metadata","lens_make=Apple","-metadata","lens_serial_number="+serial,"-metadata",`location=${location.iso6709}`,"-metadata",`GPS_latitude=${location.lat}`,"-metadata",`GPS_latitude_ref=${location.latRef}`,"-metadata",`GPS_longitude=${location.lon}`,"-metadata",`GPS_longitude_ref=${location.lonRef}`,"-metadata","GPS_altitude=50","-metadata","GPS_map_datum=WGS-84","-metadata",`GPS_date_stamp=${dateStr.substring(0,8)}`,"-metadata","media_type=Video","-metadata","hdr_format=Dolby Vision","-metadata","color_primaries=BT.2020","-metadata","stabilization=OIS","-metadata",`gyroscope_x=${g()}`,"-metadata",`gyroscope_y=${g()}`,"-metadata",`gyroscope_z=${g()}`,"-metadata",`accelerometer_x=${a()}`,"-metadata",`accelerometer_y=${a()}`,"-metadata",`accelerometer_z=${(9.8+parseFloat(a())).toFixed(6)}`,"-metadata","comment=","-metadata","artist=","-metadata","copyright=","-metadata","description=","-metadata","album=","-metadata","genre="];
}

const EMOTION_COLORS={curiosity:{word:"#FFD700",glow:"rgba(255,215,0,0.5)",power:"#FF1744"},fear:{word:"#FF4444",glow:"rgba(255,68,68,0.5)",power:"#FFD700"},hope:{word:"#00E676",glow:"rgba(0,230,118,0.5)",power:"#FFFFFF"},joy:{word:"#FF9100",glow:"rgba(255,145,0,0.5)",power:"#FFFFFF"},awe:{word:"#E040FB",glow:"rgba(224,64,251,0.5)",power:"#FFD700"},surprise:{word:"#40C4FF",glow:"rgba(64,196,255,0.5)",power:"#FFD700"},desire:{word:"#FF1744",glow:"rgba(255,23,68,0.5)",power:"#FFD700"},anger:{word:"#FF1744",glow:"rgba(255,23,68,0.5)",power:"#FFD700"},sadness:{word:"#82B1FF",glow:"rgba(130,177,255,0.5)",power:"#FFFFFF"},default:{word:"#FFFFFF",glow:"rgba(255,255,255,0.4)",power:"#FF1744"}};
const emotion=(analysis.primary_emotion||"").toLowerCase(), COLORS=EMOTION_COLORS[emotion]||EMOTION_COLORS.default;

const TAG_WORD_STYLES={shock:{colorWord:"#FFFFFF",colorGlow:"rgba(255,50,50,0.9)",scaleMult:1.30,glowSpread:80,strokeColor:"rgba(255,0,0,0.8)",strokeWidth:5,brightness:1.4},urgency:{colorWord:"#FF2200",colorGlow:"rgba(255,34,0,0.8)",scaleMult:1.20,glowSpread:60,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:4,brightness:1.3},intrigue:{colorWord:"#FFD700",colorGlow:"rgba(255,215,0,0.7)",scaleMult:1.0,glowSpread:50,strokeColor:"rgba(0,0,0,0.95)",strokeWidth:4,brightness:1.0},emotional:{colorWord:"#FF8FAB",colorGlow:"rgba(255,143,171,0.7)",scaleMult:0.95,glowSpread:45,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:4,brightness:1.0},confident:{colorWord:"#FFFFFF",colorGlow:"rgba(255,255,255,0.6)",scaleMult:1.10,glowSpread:40,strokeColor:"rgba(0,0,0,0.95)",strokeWidth:5,brightness:1.2},inspiration:{colorWord:"#FFD700",colorGlow:"rgba(255,215,0,0.8)",scaleMult:1.15,glowSpread:70,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:4,brightness:1.3},wisdom:{colorWord:"#82B1FF",colorGlow:"rgba(130,177,255,0.6)",scaleMult:0.90,glowSpread:35,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:3,brightness:0.95},desire:{colorWord:"#FFB347",colorGlow:"rgba(255,179,71,0.7)",scaleMult:1.0,glowSpread:45,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:4,brightness:1.1},calm:{colorWord:"#80DEEA",colorGlow:"rgba(128,222,234,0.5)",scaleMult:0.85,glowSpread:30,strokeColor:"rgba(0,0,0,0.85)",strokeWidth:3,brightness:0.9},information:{colorWord:"#FFFFFF",colorGlow:"rgba(255,255,255,0.35)",scaleMult:1.0,glowSpread:30,strokeColor:"rgba(0,0,0,0.95)",strokeWidth:4,brightness:1.0},pause:{colorWord:"#B0BEC5",colorGlow:"rgba(176,190,197,0.4)",scaleMult:0.80,glowSpread:25,strokeColor:"rgba(0,0,0,0.8)",strokeWidth:2,brightness:0.85},whisper:{colorWord:"#CE93D8",colorGlow:"rgba(206,147,216,0.6)",scaleMult:0.88,glowSpread:35,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:3,brightness:0.9},curiosity:{colorWord:"#FFF176",colorGlow:"rgba(255,241,118,0.6)",scaleMult:1.02,glowSpread:45,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:4,brightness:1.05},storytelling:{colorWord:"#FFCC80",colorGlow:"rgba(255,204,128,0.5)",scaleMult:0.95,glowSpread:35,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:3,brightness:1.0},dramatic:{colorWord:"#EF9A9A",colorGlow:"rgba(239,154,154,0.7)",scaleMult:1.12,glowSpread:55,strokeColor:"rgba(100,0,0,0.8)",strokeWidth:4,brightness:1.15},revelation:{colorWord:"#FFFFFF",colorGlow:"rgba(255,255,200,0.9)",scaleMult:1.25,glowSpread:75,strokeColor:"rgba(200,150,0,0.8)",strokeWidth:5,brightness:1.45},tension:{colorWord:"#FF7043",colorGlow:"rgba(255,112,67,0.75)",scaleMult:1.15,glowSpread:55,strokeColor:"rgba(0,0,0,0.9)",strokeWidth:4,brightness:1.25},climax:{colorWord:"#FFFFFF",colorGlow:"rgba(255,100,50,0.95)",scaleMult:1.35,glowSpread:90,strokeColor:"rgba(255,50,0,0.9)",strokeWidth:6,brightness:1.5},powerful:{colorWord:"#ECEFF1",colorGlow:"rgba(236,239,241,0.65)",scaleMult:1.12,glowSpread:45,strokeColor:"rgba(0,0,0,0.95)",strokeWidth:5,brightness:1.2}};
const DEFAULT_WORD_STYLE=TAG_WORD_STYLES.information;
const POWER_STYLE={colorWord:COLORS.power,colorGlow:"rgba(255,23,68,0.9)",scaleMult:1.15,glowSpread:90,strokeColor:"rgba(0,0,0,0.5)",strokeWidth:2,brightness:1.5};
function getWordStyle(tag){return TAG_WORD_STYLES[tag]||DEFAULT_WORD_STYLE;}

const TAG_TRANSITION={shock:{flashColor:"rgba(255,255,255,1.0)",flashFrames:9,shakeAmount:18,scaleBoost:1.12},urgency:{flashColor:"rgba(220,0,0,0.85)",flashFrames:7,shakeAmount:12,scaleBoost:1.08},intrigue:{flashColor:"rgba(0,0,0,0.6)",flashFrames:10,shakeAmount:5,scaleBoost:1.04},emotional:{flashColor:"rgba(255,100,150,0.35)",flashFrames:12,shakeAmount:3,scaleBoost:1.02},confident:{flashColor:"rgba(255,255,255,0.55)",flashFrames:6,shakeAmount:6,scaleBoost:1.06},inspiration:{flashColor:"rgba(255,215,0,0.6)",flashFrames:8,shakeAmount:4,scaleBoost:1.07},wisdom:{flashColor:"rgba(130,177,255,0.3)",flashFrames:14,shakeAmount:2,scaleBoost:1.01},desire:{flashColor:"rgba(255,100,180,0.4)",flashFrames:10,shakeAmount:4,scaleBoost:1.03},calm:{flashColor:"rgba(100,200,255,0.2)",flashFrames:16,shakeAmount:1,scaleBoost:1.0},information:{flashColor:"rgba(255,255,255,0.15)",flashFrames:6,shakeAmount:0,scaleBoost:1.0},pause:{flashColor:"rgba(0,0,0,0.7)",flashFrames:18,shakeAmount:0,scaleBoost:1.0},whisper:{flashColor:"rgba(100,0,150,0.4)",flashFrames:12,shakeAmount:2,scaleBoost:1.02},curiosity:{flashColor:"rgba(255,241,118,0.4)",flashFrames:10,shakeAmount:3,scaleBoost:1.03},storytelling:{flashColor:"rgba(255,200,100,0.25)",flashFrames:8,shakeAmount:1,scaleBoost:1.01},dramatic:{flashColor:"rgba(180,0,0,0.6)",flashFrames:12,shakeAmount:10,scaleBoost:1.10},revelation:{flashColor:"rgba(255,255,200,0.9)",flashFrames:10,shakeAmount:14,scaleBoost:1.15},tension:{flashColor:"rgba(255,100,0,0.5)",flashFrames:8,shakeAmount:10,scaleBoost:1.08},climax:{flashColor:"rgba(255,255,255,0.95)",flashFrames:11,shakeAmount:20,scaleBoost:1.18},powerful:{flashColor:"rgba(255,255,255,0.6)",flashFrames:7,shakeAmount:7,scaleBoost:1.07}};
const DEFAULT_TRANSITION_CFG={flashColor:"rgba(255,255,255,0.3)",flashFrames:7,shakeAmount:4,scaleBoost:1.02};

function safeKey(str,maxLen=25){return(str||"").slice(0,maxLen*2).replace(/[^a-zA-Z0-9\u0600-\u06FF]/g,"_").replace(/_+/g,"_").slice(0,maxLen);}
const esc=(s)=>(s||"").toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#039;");
function normalizeWord(w){return(w||"").toString().replace(/[.,!?؟،;:"'(){}[\]<>«»…]/g,"").trim().toLowerCase();}
const isArabic=(t)=>/[\u0600-\u06FF]/.test(t), isFrench=(t)=>/[àâçéèêëîïôùûüÿœæ]/i.test(t);
function getFontFamily(text){return isArabic(text)?`"Noto Naskh Arabic","Amiri",serif`:`"Noto Sans","DejaVu Sans",sans-serif`;}
function getDir(text){return isArabic(text)?"rtl":"ltr";}
function getLang(text){if(isArabic(text))return"ar";if(isFrench(text))return"fr";return"en";}
function probeDuration(fp){const r=spawnSync("ffprobe",["-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",fp],{stdio:["ignore","pipe","pipe"]});return parseFloat(r.stdout.toString().trim())||0;}
function runFFmpeg(args,opts={}){return spawnSync("ffmpeg",args,{stdio:["ignore","pipe","pipe"],...opts});}

const realAudioDuration=probeDuration(audio), effectiveDuration=realAudioDuration>1?realAudioDuration:duration_s, totalFrames=Math.ceil(effectiveDuration*FPS);
console.log(`🎵 Audio: ${realAudioDuration.toFixed(3)}s | Frames: ${totalFrames}`);

function isPowerWord(w){if(!power_words.length)return false;const n=normalizeWord(w);if(n.length<2)return false;return power_words.some(pw=>{const p=normalizeWord(pw);return p&&(n===p||(p.length>=3&&n.includes(p))||(n.length>=3&&p.includes(n)));});}

function detectVideoSections(){if(!aligned||aligned.length===0)return[];const sections=[],total=aligned.length,CTA=["confident","inspiration","powerful"];for(let i=0;i<total;i++){const seg=aligned[i],tag=seg.tag||"information";let st;if(i===0)st="hook";else if(i===total-1)st="cta";else if(i===total-2&&CTA.includes(tag))st="cta";else st="content";sections.push({type:st,start:parseFloat(seg.start||0),end:parseFloat(seg.end||0),idx:i,tag});}return sections;}
function getBigTransitionPoints(sections){if(sections.length<2)return[];const pts=[];for(let i=0;i<sections.length-1;i++){const c=sections[i],n=sections[i+1];if(c.type!==n.type)pts.push({time:c.end,fromType:c.type,toType:n.type,tag:n.tag,idx:i});}return pts;}

function buildSentenceBoundaryMap(){if(!aligned||aligned.length===0)return new Array(totalFrames).fill(null);const map=new Array(totalFrames).fill(null);for(let i=0;i<aligned.length-1;i++){const seg=aligned[i],et=parseFloat(seg.end||0);if(et<=0)continue;const tag=seg.tag||"information",cfg=TAG_TRANSITION[tag]||DEFAULT_TRANSITION_CFG,ef=Math.floor(et*FPS);for(let f=0;f<cfg.flashFrames;f++){const fr=ef+f;if(fr>=0&&fr<totalFrames&&map[fr]===null)map[fr]={tag,config:cfg,progress:f/Math.max(cfg.flashFrames-1,1)};}}const b=aligned.slice(0,-1).filter(s=>parseFloat(s.end||0)>0);console.log(`\n🎬 Sentence boundaries: ${b.length}`);b.forEach(x=>console.log(`   [${x.tag||"info"}] @ ${parseFloat(x.end).toFixed(3)}s`));return map;}

function buildClipPlan(){if(clip_durations&&clip_durations.length>0){let o=0;const p=clip_durations.map((d,i)=>{const e={index:i,start:parseFloat(o.toFixed(3)),duration:parseFloat(Math.max(d,0.5).toFixed(3)),videoPath:videos[i%videos.length],isHook:i===0&&has_hook&&isShort};o+=e.duration;return e;});logClipPlan(p);return p;}const tc=Math.max(1,Math.floor(effectiveDuration/clip_duration)),acd=effectiveDuration/tc;const p=Array.from({length:tc},(_,i)=>({index:i,start:parseFloat((i*acd).toFixed(3)),duration:parseFloat(acd.toFixed(3)),videoPath:videos[i%videos.length],isHook:i===0&&has_hook&&isShort}));logClipPlan(p);return p;}
function logClipPlan(p){console.log(`\n📋 Clip plan: ${p.length} clips [${content_mode.toUpperCase()}]`);p.forEach(c=>console.log(`   [${c.index+1}] ${c.start.toFixed(2)}s → ${(c.start+c.duration).toFixed(2)}s (${c.duration.toFixed(2)}s)${c.isHook?" 🔥":""}`));}

function buildWordList(){const w=[];for(const seg of aligned){if(!seg.words||seg.words.length===0)continue;const st=seg.tag||"information";for(const x of seg.words){if(!x.word)continue;const s=parseFloat(x.start),e=parseFloat(x.end);if(isNaN(s)||isNaN(e)||s<0||e<=s)continue;w.push({word:x.word.trim(),start:s,end:e,tag:st,isPower:isPowerWord(x.word)});}}if(w.length===0&&sentences.length>0){console.log("⚠️  No word alignment — equal split");const a=sentences.join(" ").split(/\s+/).filter(Boolean),p=effectiveDuration/Math.max(a.length,1);for(let i=0;i<a.length;i++)w.push({word:a[i],start:i*p,end:(i+1)*p,tag:"information",isPower:isPowerWord(a[i])});}w.sort((a,b)=>a.start-b.start);console.log(`📊 Words: ${w.length}`);if(w.length>0){const f=w[0],l=w[w.length-1];console.log(`   [0]  ${f.start.toFixed(3)}s → ${f.end.toFixed(3)}s "${f.word}" [${f.tag}]`);console.log(`   [-1] ${l.start.toFixed(3)}s → ${l.end.toFixed(3)}s "${l.word}" [${l.tag}]`);}return w;}

function buildFrameStateMap(words){const map=new Array(totalFrames).fill(null);if(!words.length)return map;let wi=0;for(let f=0;f<totalFrames;f++){const t=f/FPS;while(wi<words.length-1&&t>=words[wi].end)wi++;const w=words[wi];if(t>=w.start&&t<w.end)map[f]={word:w.word,tag:w.tag,isPower:w.isPower,progress:(t-w.start)/Math.max(w.end-w.start,0.001)};}const c=map.filter(Boolean).length;console.log(`Coverage: ${c}/${totalFrames} (${((c/totalFrames)*100).toFixed(1)}%)`);return map;}

function buildSentenceMap(){if(!aligned||aligned.length===0)return new Array(totalFrames).fill(null);const map=new Array(totalFrames).fill(null);for(const seg of aligned){const ss=Math.floor(parseFloat(seg.start||0)*FPS),se=Math.ceil(parseFloat(seg.end||0)*FPS),sn=seg.sentence||"";for(let f=ss;f<se&&f<totalFrames;f++)map[f]=sn;}return map;}

function stateKey(state,gf,ts){if(gf<INTRO_FRAMES)return`intro_f${gf}`;if(gf>=totalFrames-OUTRO_FRAMES)return`outro_f${gf}`;if(ts){const pb=ts.progress<0.5?"in":"out";return`tr_${ts.tag}_${pb}_${safeKey(state?state.word:"empty",15)}_${state?.isPower?1:0}`;}const h=gf<HOOK_FRAMES?"h":"n";if(!state)return`empty_${h}`;const p=state.progress,b=p<0.15?"pop":p>0.85?"fade":"hold";return`w_${safeKey(state.word,15)}_${state.tag}_${state.isPower?1:0}_${h}_${b}`;}
function longStateKey(ws,ts,sn){const wk=ws?`${safeKey(ws.word,15)}_${ws.tag}_${ws.isPower?1:0}`:"empty";const sk=safeKey(sn,25),tk=ts?`tr_${ts.tag}_${Math.floor(ts.progress*4)}`:"n";let pb="hold";if(ws)pb=ws.progress<0.15?"pop":ws.progress>0.85?"fade":"hold";return`long_${wk}_${tk}_${pb}_${sk}`;}

function computeTitleAnimation(gf){if(gf<INTRO_FRAMES){const t=gf/INTRO_FRAMES,e=1-Math.pow(1-t,3);return{opacity:e,translateY:(1-e)*-80};}if(gf>=totalFrames-OUTRO_FRAMES){const t=(gf-(totalFrames-OUTRO_FRAMES))/OUTRO_FRAMES,e=Math.pow(t,2);return{opacity:1-e,translateY:e*-60};}return{opacity:1.0,translateY:0};}
function computeWordAnimation(p,sm){if(p<0.15){const t=p/0.15,e=1-Math.pow(1-t,2);return{scale:0.6+e*0.48,opacity:Math.min(1,t*3),translateY:(1-e)*30};}if(p>0.85){const t=(p-0.85)/0.15;return{scale:1.0-t*0.05,opacity:1-t*0.3,translateY:0};}return{scale:sm,opacity:1.0,translateY:0};}
function computeTransitionEffect(ts,gf){if(!ts)return{flashOpacity:0,flashColor:"rgba(0,0,0,0)",shakeX:0,shakeY:0,transScale:1.0};const{config:c,progress:tp}=ts;let fo=tp<0.3?tp/0.3:1-(tp-0.3)/0.7;fo=Math.max(0,Math.min(1,fo));let sx=0,sy=0;if(c.shakeAmount>0){const s=c.shakeAmount*(1-tp);sx=Math.sin(gf*2.3)*s;sy=Math.cos(gf*1.7)*s;}let ts2=1.0;if(c.scaleBoost>1.0&&tp<0.5)ts2=1.0+(c.scaleBoost-1.0)*(1-tp*2);return{flashOpacity:fo,flashColor:c.flashColor,shakeX:sx,shakeY:sy,transScale:ts2};}

const SFS=[{maxLen:2,ar:170,en:160},{maxLen:4,ar:150,en:140},{maxLen:6,ar:130,en:120},{maxLen:9,ar:110,en:102},{maxLen:12,ar:92,en:86},{maxLen:99,ar:76,en:72}];
const LFS=[{maxLen:2,ar:130,en:120},{maxLen:4,ar:110,en:100},{maxLen:6,ar:95,en:86},{maxLen:9,ar:80,en:72},{maxLen:12,ar:68,en:62},{maxLen:99,ar:56,en:52}];
function computeFontSize(w,isAr,sm,isL){if(!w)return 100;const wl=w.length,t=isL?LFS:SFS,mn=isL?48:60,mx=isL?160:220;let bs=isL?80:100;for(const{maxLen:ml,ar,en}of t){if(wl<=ml){bs=isAr?ar:en;break;}}bs=Math.round(bs*sm);return Math.max(mn,Math.min(mx,bs));}

const HOOK_DEFAULTS={ar:"🔴 لا تتجاوز هذا",fr:"🔴 Ne ratez pas ça",en:"🔴 Don't skip this"};
function getHookText(){return(custom_hook&&custom_hook.trim())||HOOK_DEFAULTS[lang]||HOOK_DEFAULTS.en;}
// ═══════════════════════════════════════════════════════════════════════════
// HTML BUILDERS
// ═══════════════════════════════════════════════════════════════════════════

function buildHTMLShort({word,tag="information",isPower=false,isHook=false,globalFrame=0,progress=0.5,transitionState=null}){
  const ar=word?isArabic(word):false,dir=word?getDir(word):"ltr",font=word?getFontFamily(word):`"Noto Sans",sans-serif`,la=word?getLang(word):"en";
  const td=getDir(display_title),tf=getFontFamily(display_title),ts=isPower?POWER_STYLE:getWordStyle(tag);
  const ta=computeTitleAnimation(globalFrame),wa=word?computeWordAnimation(progress,ts.scaleMult):{scale:1,opacity:0,translateY:0};
  const tr=computeTransitionEffect(transitionState,globalFrame),fs=computeFontSize(word,ar,ts.scaleMult,false);
  const fsc=wa.scale*tr.transScale,fo=word?wa.opacity:0;
  const wt=`translate(-50%,calc(-50% + ${wa.translateY.toFixed(1)}px)) translate(${tr.shakeX.toFixed(2)}px,${tr.shakeY.toFixed(2)}px) scale(${fsc.toFixed(4)})`;
  const ht=getHookText(),ha=isArabic(ht),hd=ha?"rtl":"ltr",hf=getFontFamily(ht);
  const tia=isArabic(display_title),tfs=tia?52:46,es=tia?56:50;
  const pcs=isPower?`background:linear-gradient(135deg,#FF1744,#D50000);padding:24px 60px;border-radius:9999px;border:3px solid rgba(255,255,255,0.3);box-shadow:0 0 60px rgba(255,23,68,0.8),0 0 120px rgba(255,23,68,0.4);`:`background:transparent;padding:0;`;
  const wts=isPower?`font-family:${font};font-size:${fs}px;font-weight:900;color:#FFF;line-height:1.15;letter-spacing:${ar?"1px":"3px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;`:`font-family:${font};font-size:${fs}px;font-weight:900;color:${ts.colorWord};line-height:1.15;letter-spacing:${ar?"1px":"3px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;text-shadow:0 0 ${ts.glowSpread}px ${ts.colorGlow},0 0 ${ts.glowSpread*1.5}px ${ts.colorGlow};filter:brightness(${ts.brightness});`;
  return`<!DOCTYPE html><html lang="${la}"><head><meta charset="UTF-8"/><style>*{margin:0;padding:0;box-sizing:border-box;}html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}.ot{position:absolute;top:0;left:0;right:0;height:40%;background:linear-gradient(to bottom,rgba(0,0,0,0.85) 0%,rgba(0,0,0,0.5) 50%,transparent 100%);pointer-events:none;z-index:1;}.ob{position:absolute;bottom:0;left:0;right:0;height:42%;background:linear-gradient(to top,rgba(0,0,0,0.88) 0%,rgba(0,0,0,0.45) 65%,transparent 100%);pointer-events:none;z-index:1;}.flash{position:absolute;inset:0;background:${tr.flashColor};opacity:${tr.flashOpacity.toFixed(4)};pointer-events:none;z-index:50;}.tc{position:absolute;top:410px;left:50%;width:92%;max-width:980px;direction:${td};text-align:center;z-index:30;transform:translateX(-50%) translateY(${ta.translateY.toFixed(2)}px);opacity:${ta.opacity.toFixed(4)};}.tc::after{content:'';display:block;margin:16px auto 0;width:120px;height:4px;border-radius:2px;background:linear-gradient(90deg,transparent,#FF1744,transparent);opacity:${ta.opacity.toFixed(4)};}.tt{font-family:${tf};font-size:${tfs}px;font-weight:900;color:#FFF;display:inline-flex;align-items:center;justify-content:center;gap:14px;line-height:1.3;direction:${td};-webkit-text-stroke:2px rgba(0,0,0,0.8);paint-order:stroke fill;text-shadow:0 0 30px rgba(255,23,68,0.6),0 4px 20px rgba(0,0,0,0.9),2px 2px 0 rgba(0,0,0,0.8);}.te{font-size:${es}px;-webkit-text-stroke:0;}.hb{position:absolute;top:${tia?"290px":"270px"};left:50%;transform:translateX(-50%);background:linear-gradient(135deg,rgba(220,0,0,0.95),rgba(160,0,0,0.95));color:#fff;font-family:${hf};font-size:${ha?"32px":"28px"};font-weight:900;padding:12px 38px;border-radius:9999px;z-index:25;white-space:nowrap;direction:${hd};border:2px solid rgba(255,120,120,0.4);box-shadow:0 0 50px rgba(220,0,0,0.7),0 8px 24px rgba(0,0,0,0.5);}.wc{position:absolute;left:50%;top:54%;transform:${wt};opacity:${fo.toFixed(4)};direction:${dir};text-align:center;z-index:10;width:95%;max-width:1020px;}.wp{display:inline-block;${pcs}}.wt{${wts}}</style></head><body><div class="ot"></div><div class="ob"></div><div class="flash"></div><div class="tc"><div class="tt"><span class="te">${emoji_left}</span><span>${esc(display_title)}</span><span class="te">${emoji_right}</span></div></div>${isHook?`<div class="hb">${esc(ht)}</div>`:""}${word?`<div class="wc"><div class="wp"><span class="wt">${esc(word)}</span></div></div>`:""}</body></html>`;
}

function buildHTMLLong({word,tag="information",isPower=false,globalFrame=0,progress=0.5,transitionState=null,currentSentence="",highlightedWord=""}){
  const ar=word?isArabic(word):false,dir=word?getDir(word):"ltr",font=word?getFontFamily(word):`"Noto Sans",sans-serif`,la=word?getLang(word):"en";
  const td=getDir(display_title),tf=getFontFamily(display_title),sd=currentSentence?getDir(currentSentence):"ltr",sf=currentSentence?getFontFamily(currentSentence):`"Noto Sans",sans-serif`;
  const ts=getWordStyle(tag);let to=1.0;if(globalFrame<INTRO_FRAMES)to=globalFrame/INTRO_FRAMES;else if(globalFrame>=totalFrames-OUTRO_FRAMES)to=(totalFrames-globalFrame)/OUTRO_FRAMES;
  let ws=ts.scaleMult,wo=word?1.0:0;if(word&&progress<0.15){const t=progress/0.15;ws=0.6+(1-Math.pow(1-t,2))*(ts.scaleMult-0.6);wo=Math.min(1,t*3);}else if(word&&progress>0.85){wo=1-((progress-0.85)/0.15)*0.3;}
  let fo2=0,fc="rgba(0,0,0,0)",sx=0,sy=0;if(transitionState){const{config:c,progress:tp}=transitionState;fo2=tp<0.3?tp/0.3:1-(tp-0.3)/0.7;fo2=Math.max(0,Math.min(1,fo2));fc=c.flashColor;if(c.shakeAmount>0){const s=c.shakeAmount*0.5*(1-tp);sx=Math.sin(globalFrame*2.3)*s;sy=Math.cos(globalFrame*1.7)*s;}}
  const fs=computeFontSize(word,ar,ts.scaleMult,true),wt=`translate(-50%,-50%) translate(${sx.toFixed(2)}px,${sy.toFixed(2)}px) scale(${ws.toFixed(4)})`;
  const wts=`font-family:${font};font-size:${fs}px;font-weight:900;color:${ts.colorWord};line-height:1.2;letter-spacing:${ar?"1px":"2px"};display:block;word-break:break-word;-webkit-text-stroke:${ts.strokeWidth}px ${ts.strokeColor};paint-order:stroke fill;text-shadow:0 0 ${ts.glowSpread}px ${ts.colorGlow},0 0 ${ts.glowSpread*1.5}px ${ts.colorGlow};filter:brightness(${ts.brightness});`;
  const sh=currentSentence?currentSentence.split(/\s+/).map(w=>{const h=normalizeWord(w)===normalizeWord(highlightedWord);return h?`<span class="sh">${esc(w)}</span>`:`<span class="sw">${esc(w)}</span>`;}).join(" "):"";
  const tia=isArabic(display_title),tfs=tia?36:32,es=tia?38:34;
  return`<!DOCTYPE html><html lang="${la}"><head><meta charset="UTF-8"/><style>*{margin:0;padding:0;box-sizing:border-box;}html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}.ot{position:absolute;top:0;left:0;right:0;height:35%;background:linear-gradient(to bottom,rgba(0,0,0,0.8) 0%,transparent 100%);pointer-events:none;z-index:1;}.ob{position:absolute;bottom:0;left:0;right:0;height:38%;background:linear-gradient(to top,rgba(0,0,0,0.92) 0%,rgba(0,0,0,0.5) 60%,transparent 100%);pointer-events:none;z-index:1;}.flash{position:absolute;inset:0;background:${fc};opacity:${fo2.toFixed(4)};pointer-events:none;z-index:50;}.tc{position:absolute;top:28px;${td==="rtl"?"right:40px":"left:40px"};direction:${td};text-align:${td==="rtl"?"right":"left"};z-index:30;opacity:${to.toFixed(4)};}.tt{font-family:${tf};font-size:${tfs}px;font-weight:900;color:#FFF;display:inline-flex;align-items:center;gap:10px;line-height:1.2;direction:${td};-webkit-text-stroke:1px rgba(0,0,0,0.8);paint-order:stroke fill;text-shadow:0 0 20px rgba(255,23,68,0.5),0 2px 10px rgba(0,0,0,0.9);}.te{font-size:${es}px;-webkit-text-stroke:0;}.tline{display:block;margin-top:8px;width:80px;height:3px;border-radius:2px;background:#FF1744;}.wc{position:absolute;left:50%;top:46%;transform:${wt};opacity:${wo.toFixed(4)};direction:${dir};text-align:center;z-index:10;width:80%;max-width:1400px;}.wt{${wts}}.subtitle{position:absolute;bottom:48px;left:60px;right:60px;direction:${sd};text-align:center;z-index:20;font-family:${sf};font-size:${ar?"34px":"30px"};font-weight:700;line-height:1.6;}.sw{color:rgba(255,255,255,0.65);-webkit-text-stroke:1px rgba(0,0,0,0.6);paint-order:stroke fill;display:inline;}.sh{color:#FFD700;-webkit-text-stroke:1px rgba(0,0,0,0.8);paint-order:stroke fill;display:inline;text-shadow:0 0 20px rgba(255,215,0,0.8);font-weight:900;}</style></head><body><div class="ot"></div><div class="ob"></div><div class="flash"></div><div class="tc"><div class="tt">${td==="rtl"?`<span>${esc(display_title)}</span><span class="te">${emoji_left}</span>`:`<span class="te">${emoji_left}</span><span>${esc(display_title)}</span>`}</div><span class="tline"></span></div>${word?`<div class="wc"><span class="wt">${esc(word)}</span></div>`:""}${sh?`<div class="subtitle">${sh}</div>`:""}</body></html>`;
}

// ═══════════════════════════════════════════════════════════════════════════
// BROWSER + RENDER PNGs
// ═══════════════════════════════════════════════════════════════════════════

async function launchBrowser(){const b=await chromium.launch({headless:true,args:BROWSER_ARGS});const c=await b.newContext({viewport:{width:WIDTH,height:HEIGHT},deviceScaleFactor:1,locale:"ar-SA"});const p=await c.newPage();return{browser:b,page:p};}
async function warmupFonts(page,builder){for(const[w,l]of[["مرحبا","ar"],["Hello","en"]]){const html=builder({word:w,tag:"information",isPower:false,isHook:false,globalFrame:TITLE_SLIDE_FRAMES,progress:0.5});const p=join(TMP,`init_${l}.html`);writeFileSync(p,html,"utf-8");await page.goto(`file://${p}`,{waitUntil:"networkidle"});await page.waitForTimeout(l==="ar"?1000:500);}console.log("✅ Fonts loaded");}

function collectUniqueShortStates(fsm,bm){const u=new Map();for(let f=0;f<fsm.length;f++){const ts=bm[f]||null,k=stateKey(fsm[f],f,ts);if(!u.has(k))u.set(k,{word:fsm[f]?.word??null,tag:fsm[f]?.tag??"information",isPower:fsm[f]?.isPower??false,isHook:f<HOOK_FRAMES,globalFrame:f,progress:fsm[f]?.progress??0.5,transitionState:ts});}return u;}
async function renderAllPNGsShort(page,fsm,bm){const u=collectUniqueShortStates(fsm,bm);console.log(`\n📸 ${u.size} unique states [SHORT]`);await warmupFonts(page,buildHTMLShort);const cache=new Map();let d=0;for(const[k,s]of u){const html=buildHTMLShort(s),hp=join(TMP,`${k}.html`);writeFileSync(hp,html,"utf-8");await page.goto(`file://${hp}`,{waitUntil:"load"});await page.waitForTimeout(35);const pp=join(TMP,`${k}.png`);await page.screenshot({path:pp,type:"png",omitBackground:true});cache.set(k,pp);d++;if(d%50===0||d===u.size)process.stdout.write(`  ${d}/${u.size} PNGs\n`);}return cache;}

function collectUniqueLongStates(fsm,bm,sm){const u=new Map();for(let f=0;f<fsm.length;f++){const ts=bm[f]||null,ws=fsm[f],sn=sm[f]||"",k=longStateKey(ws,ts,sn);if(!u.has(k))u.set(k,{word:ws?.word??null,tag:ws?.tag??"information",isPower:ws?.isPower??false,globalFrame:f,progress:ws?.progress??0.5,transitionState:ts,currentSentence:sn,highlightedWord:ws?.word??""});}return u;}
async function renderAllPNGsLong(page,fsm,bm,sm){const u=collectUniqueLongStates(fsm,bm,sm);console.log(`\n📸 ${u.size} unique states [LONG]`);await warmupFonts(page,buildHTMLLong);const cache=new Map();let d=0;for(const[k,s]of u){const html=buildHTMLLong(s),hp=join(TMP,`${k}.html`);writeFileSync(hp,html,"utf-8");await page.goto(`file://${hp}`,{waitUntil:"load"});await page.waitForTimeout(35);const pp=join(TMP,`${k}.png`);await page.screenshot({path:pp,type:"png",omitBackground:true});cache.set(k,pp);d++;if(d%50===0||d===u.size)process.stdout.write(`  ${d}/${u.size} PNGs\n`);}return cache;}

// ═══════════════════════════════════════════════════════════════════════════
// VIDEO FILTERS (✅ شدة 180)
// ═══════════════════════════════════════════════════════════════════════════

function buildDramaticLightingFilter(){return`geq=r='clip(r(X,Y)+if(lte(X,W/2),180*(1-X/(W/2)),0),0,255)':g='g(X,Y)':b='clip(b(X,Y)+if(gte(X,W/2),180*((X-W/2)/(W/2)),0),0,255)'`;}
function buildZoomOutFilter(d,i){const f=Math.ceil(d*FPS),sz=(1.25+(i%3)*0.05).toFixed(3);return`scale=w='trunc((iw*(${sz}-(${sz}-1.02)*min(on,${f})/${f}))/2)*2':h='trunc((ih*(${sz}-(${sz}-1.02)*min(on,${f})/${f}))/2)*2'`;}
function buildCameraShakeFilter(i){const f1=(0.8+(i%3)*0.3).toFixed(2),f2=(0.5+(i%2)*0.4).toFixed(2),ax=3+(i%2),ay=2+(i%2);return`crop=${WIDTH}:${HEIGHT}:'(iw-${WIDTH})/2+${ax}*sin(2*PI*${f1}*t)':'(ih-${HEIGHT})/2+${ay}*sin(2*PI*${f2}*t+1)'`;}
function buildBreathingFilter(i){const hz=(0.3+(i%2)*0.1).toFixed(2);return`scale=w='iw*(1+0.006*sin(2*PI*${hz}*t))':h='ih*(1+0.006*sin(2*PI*${hz}*t))',crop=${WIDTH}:${HEIGHT}`;}
function buildFilmLookFilter(){return`curves=r='0/0 0.25/0.20 0.5/0.55 0.75/0.80 1/0.95':g='0/0 0.25/0.22 0.5/0.50 0.75/0.78 1/0.92':b='0/0.05 0.25/0.28 0.5/0.55 0.75/0.82 1/1.0'`;}
function buildSplitToningFilter(){return`curves=r='0/0.02 0.5/0.52 1/0.98':g='0/0 0.5/0.50 1/1.0':b='0/0.05 0.5/0.48 1/0.95'`;}
function buildVignetteFilter(){return`vignette=PI/5:eval=frame`;}
function buildFilmGrainFilter(i){return`noise=alls=${4+(i%3)}:allf=t+u`;}
function buildFlickerFilter(i){return`lutyuv=y='val*(1+0.015*sin(2*PI*${(8+(i%4)).toFixed(1)}*t))'`;}
function buildColorGrading(h){return h?`eq=contrast=1.2:brightness=-0.04:saturation=0.85`:`eq=contrast=1.12:brightness=-0.02:saturation=0.88`;}
function buildOriginalityFilters(i){const h=i%2===0?3:-3,s=(1.03+(i%3)*0.02).toFixed(2),sh=(0.35+(i%2)*0.1).toFixed(2);return`hue=h=${h}:s=${s},unsharp=3:3:${sh}:3:3:0.0`;}

// ═══════════════════════════════════════════════════════════════════════════
// PROCESS BACKGROUND (مرحلتان)
// ═══════════════════════════════════════════════════════════════════════════

function processBackground(vp,dur,out,idx,isHook=false){
  const d=Math.max(dur,0.5),fi=Math.min(0.3,d*0.08),fo=Math.min(0.3,d*0.08);
  const sd=probeDuration(vp),nd=d*1.4,la=sd>0&&sd<nd?["-stream_loop","-1"]:[];
  const t1=join(TMP,`s1_${String(idx).padStart(3,"0")}.mp4`);
  const vf=`setpts=1.333*PTS,hflip,${buildZoomOutFilter(d,idx)},${buildBreathingFilter(idx)},${buildCameraShakeFilter(idx)},${buildColorGrading(isHook)},${buildFilmLookFilter()},${buildSplitToningFilter()},${buildVignetteFilter()},${buildFlickerFilter(idx)},${buildFilmGrainFilter(idx)},${buildOriginalityFilters(idx)},fade=t=in:st=0:d=${fi.toFixed(3)},fade=t=out:st=${(d-fo).toFixed(3)}:d=${fo.toFixed(3)}`;
  let r=runFFmpeg(["-y",...la,"-i",vp,"-t",(d*1.4).toFixed(3),"-vf",vf,"-r",String(FPS),"-c:v","libx264","-preset","fast","-crf",isHook?"16":"18","-pix_fmt","yuv420p","-an",t1]);
  if(r.status!==0){console.log(`  ⚠️  S1 full fail [${idx}]`);const vs=`setpts=1.333*PTS,hflip,scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1,${buildColorGrading(isHook)},${buildFilmGrainFilter(idx)},${buildOriginalityFilters(idx)},fade=t=in:st=0:d=${fi.toFixed(3)},fade=t=out:st=${(d-fo).toFixed(3)}:d=${fo.toFixed(3)}`;r=runFFmpeg(["-y",...la,"-i",vp,"-t",(d*1.4).toFixed(3),"-vf",vs,"-r",String(FPS),"-c:v","libx264","-preset","fast","-crf","21","-pix_fmt","yuv420p","-an",t1]);if(r.status!==0)runFFmpeg(["-y","-stream_loop","-1","-i",vp,"-t",d.toFixed(3),"-vf",`scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=increase,crop=${WIDTH}:${HEIGHT},setsar=1`,"-r",String(FPS),"-c:v","libx264","-preset","fast","-crf","23","-pix_fmt","yuv420p","-an",t1]);}
  const r2=runFFmpeg(["-y","-i",t1,"-vf",buildDramaticLightingFilter(),"-c:v","libx264","-preset","fast","-crf",isHook?"16":"18","-pix_fmt","yuv420p","-an",out]);
  if(r2.status!==0){console.log(`  ⚠️  Light fail [${idx}]`);copyFileSync(t1,out);}else console.log(`  ✅ 🔴🔵 Light [${idx}]`);
  try{spawnSync("rm",["-f",t1],{stdio:"ignore"});}catch{}
  return out;
}

// ═══════════════════════════════════════════════════════════════════════════
// TRANSITIONS (✅ slide_burst محسّن)
// ═══════════════════════════════════════════════════════════════════════════

function buildTransitionFrameHTML(tt,progress,fc,idx){
  const t=progress;let fop=0,fw=0,vt="scale(1)",flop=0,go=0,parts="";
  switch(tt){
    case"slide_burst":{
      if(t<0.15){fop=t/0.15;fw=25;flop=(t/0.15)*0.3;}
      else if(t<0.6){const s=(t-0.15)/0.45;fop=1;fw=25+s*10;vt=`translateX(${-s*WIDTH}px)`;flop=0.3+s*0.4;}
      else if(t<0.85){const e=(t-0.6)/0.25;fop=1;fw=35-e*15;vt=`translateX(${WIDTH*(1-e)}px)`;flop=0.7-e*0.5;}
      else{const e=(t-0.85)/0.15;fop=1-e;fw=20-e*20;flop=0.2*(1-e);}
      break;}
    case"zoom_punch":{if(t<0.3){const z=t/0.3;vt=`scale(${1+z*0.2})`;fop=z;fw=15+z*10;}else if(t<0.6){const s=(t-0.3)/0.3;vt=`scale(${1.2-s*1.2})`;fop=1;fw=25;}else{const n=(t-0.6)/0.4;vt=`scale(${n})`;fop=1-n;flop=(1-n)*0.4;}break;}
    case"spin_crash":{if(t<0.2){fop=t/0.2;fw=20;flop=(t/0.2)*0.6;}else if(t<0.7){const s=(t-0.2)/0.5;vt=`rotate(${s*90}deg) scale(${1-s*0.7})`;fop=1;fw=25;}else{const n=(t-0.7)/0.3;vt=`rotate(${-90+n*90}deg) scale(${0.3+n*0.7})`;fop=1-n;}break;}
    case"flash_cut":{if(t<0.4){flop=t/0.4;fop=(t/0.4)*0.3;}else if(t<0.6)flop=1;else flop=1-(t-0.6)/0.4;break;}
    case"cinema_frame":{if(t<0.4){fop=t/0.4;fw=(t/0.4)*40;vt=`scale(${1-(t/0.4)*0.2})`;}else if(t<0.7){fop=1;fw=40;vt="scale(0.8)";flop=((t-0.4)/0.3)*0.3;}else{const n=(t-0.7)/0.3;fop=1-n;fw=40-n*40;vt=`scale(${0.8+n*0.2})`;}break;}
    case"glitch_wave":{if(t<0.3){go=Math.sin(t*50)*10;fop=t/0.3;fw=15;}else if(t<0.7){go=Math.sin(t*80)*20;fop=1;fw=20;flop=Math.sin(t*30)*0.3;}else{go=Math.sin(t*30)*5;fop=1-(t-0.7)/0.3;}break;}
    case"liquid_sweep":{if(t<0.5){const s=t/0.5;fop=s;fw=30;vt=`translateX(${-WIDTH+s*WIDTH}px)`;}else{const n=(t-0.5)/0.5;fop=1-n*0.5;fw=30-n*20;}break;}
    case"reveal_drop":{if(t<0.3){fop=t/0.3;fw=20;vt=`translateY(${-HEIGHT+(t/0.3)*HEIGHT}px)`;}else if(t<0.7){const d=(t-0.3)/0.4;fop=1;fw=25;vt=`translateY(${d*HEIGHT}px)`;}else{const n=(t-0.7)/0.3;fop=1-n;}break;}
  }
  if(t>0.3&&t<0.7){for(let i=0;i<15;i++){const px=Math.random()*WIDTH,py=Math.random()*HEIGHT,sz=3+Math.random()*5;parts+=`<div style="position:absolute;left:${px}px;top:${py}px;width:${sz}px;height:${sz}px;background:${fc};border-radius:50%;box-shadow:0 0 ${sz*2}px ${fc};opacity:0.8;z-index:60;"></div>`;}}
  return`<!DOCTYPE html><html><head><meta charset="UTF-8"/><style>*{margin:0;padding:0;box-sizing:border-box;}html,body{width:${WIDTH}px;height:${HEIGHT}px;overflow:hidden;background:transparent;}.frame{position:absolute;inset:0;border:${fw}px solid ${fc};opacity:${fop};box-shadow:0 0 ${fw*2}px ${fc},inset 0 0 ${fw}px ${fc};pointer-events:none;z-index:55;}.flash{position:absolute;inset:0;background:${fc};opacity:${flop};pointer-events:none;z-index:50;}.glitch{position:absolute;inset:0;transform:translateX(${go}px);pointer-events:none;z-index:45;}</style></head><body><div class="frame"></div><div class="flash"></div><div class="glitch"></div>${parts}</body></html>`;
}

async function renderTransitionOverlay(page,tt,fc,idx){
  const fd=join(TMP,`trans_${idx}`);mkdirSync(fd,{recursive:true});
  console.log(`  🎬 Rendering transition [${tt}]...`);
  for(let f=0;f<TRANSITION_FRAMES;f++){const p=f/(TRANSITION_FRAMES-1),html=buildTransitionFrameHTML(tt,p,fc,idx),hp=join(fd,`t${f}.html`);writeFileSync(hp,html,"utf-8");await page.goto(`file://${hp}`,{waitUntil:"load"});await page.waitForTimeout(15);const pp=join(fd,`frame_${String(f).padStart(6,"0")}.png`);await page.screenshot({path:pp,type:"png",omitBackground:true});}
  const mv=join(TMP,`trans_${idx}.mov`);runFFmpeg(["-y","-framerate",String(FPS),"-i",`${fd}/frame_%06d.png`,"-c:v","png","-an",mv]);return mv;
}

async function applyTransitionsToVideo(page,bgVid,btp,outPath){
  if(!btp||btp.length===0){console.log("  ℹ️  No big transitions");copyFileSync(bgVid,outPath);return outPath;}
  console.log(`\n  🎬 Applying ${btp.length} pro transitions...`);let cv=bgVid;const tf=[];
  for(let i=0;i<btp.length;i++){const pt=btp[i],tt=PRO_TRANSITION_TYPES[i%PRO_TRANSITION_TYPES.length],fc=TAG_FRAME_COLORS[pt.tag]||"#FF1744";
    console.log(`     [${i+1}/${btp.length}] @${pt.time.toFixed(2)}s — ${tt} (${pt.tag})`);
    const tm=await renderTransitionOverlay(page,tt,fc,i),no=join(TMP,`wt_${i}.mp4`);tf.push(no);
    const st=Math.max(0,pt.time-TRANSITION_DURATION/2);
    const r=runFFmpeg(["-y","-i",cv,"-i",tm,"-filter_complex",`[1:v]format=rgba,setpts=PTS+${st}/TB[overlay];[0:v][overlay]overlay=0:0:enable='between(t,${st},${st+TRANSITION_DURATION})':format=auto[out]`,"-map","[out]","-map","0:a?","-c:v","libx264","-preset","fast","-crf","19","-c:a","copy","-pix_fmt","yuv420p",no]);
    if(r.status===0)cv=no;else console.log(`  ⚠️  Trans ${i} failed`);}
  copyFileSync(cv,outPath);tf.forEach(f=>{try{spawnSync("rm",["-f",f],{stdio:"ignore"});}catch{}});
  console.log(`  ✅ Pro transitions applied`);return outPath;
}

// ═══════════════════════════════════════════════════════════════════════════
// FFMPEG OPS + METADATA + MERGE
// ═══════════════════════════════════════════════════════════════════════════

function framesToMov(fd,out){runFFmpeg(["-y","-framerate",String(FPS),"-i",`${fd}/frame_%06d.png`,"-vf",`scale=${WIDTH}:${HEIGHT},format=rgba`,"-c:v","png","-an",out]);return out;}

function overlayOnBg(bg,cap,aud,out){
  const of2="[1:v]format=rgba[cap];[0:v][cap]overlay=0:0:format=auto,format=yuv420p[out]";
  let r=runFFmpeg(["-y","-i",bg,"-i",cap,"-filter_complex",of2,"-map","[out]","-map","0:a:0","-c:v","libx264","-preset","fast","-crf","19","-c:a","aac","-b:a","192k","-pix_fmt","yuv420p",out]);
  if(r.status!==0&&aud){console.log("  ⚠️  Audio fallback...");r=runFFmpeg(["-y","-i",bg,"-i",cap,"-i",aud,"-filter_complex",of2,"-map","[out]","-map","2:a:0","-c:v","libx264","-preset","fast","-crf","19","-c:a","aac","-b:a","192k","-pix_fmt","yuv420p",out]);}
  if(r.status!==0)console.error("❌ overlayOnBg failed");return out;
}

function xfadeConcat(clips,durs){
  if(clips.length===0)return"";if(clips.length===1)return clips[0];
  const X=isLong?0.5:0.3,fl=[];let o=0,l="[0:v]";
  for(let i=1;i<clips.length;i++){o+=durs[i-1]-X;if(o<0)o=0;const ot=i===clips.length-1?"[vout]":`[v${i}]`,tr=XFADE_TRANSITIONS[(i-1)%XFADE_TRANSITIONS.length];fl.push(`${l}[${i}:v]xfade=transition=${tr}:duration=${X}:offset=${o.toFixed(3)}${ot}`);l=ot;}
  const op=join(TMP,"xfaded.mp4"),r=runFFmpeg(["-y",...clips.flatMap(p=>["-i",p]),"-filter_complex",fl.join(";"),"-map","[vout]","-c:v","libx264","-preset","fast","-crf","18","-pix_fmt","yuv420p","-an",op]);
  if(r.status!==0){const ls=join(TMP,"list.txt");writeFileSync(ls,clips.map(p=>`file '${p}'`).join("\n"));const rw=join(TMP,"raw.mp4");spawnSync("ffmpeg",["-y","-f","concat","-safe","0","-i",ls,"-c","copy",rw],{stdio:"inherit"});return rw;}return op;
}

function applyMetadata(inp,out){const m=buildiPhoneMetadata(),r=runFFmpeg(["-y","-i",inp,"-c","copy",...m,out]);if(r.status!==0){console.log("  ⚠️  Metadata failed");copyFileSync(inp,out);}else console.log(`  ✅ Metadata: 📱 iPhone 17 Pro Max | 📍 ${location.city} | 📅 ${new Date().toLocaleDateString()}`);}

function mergeAudio(vp,ap,out){
  const ad=probeDuration(ap),vd=probeDuration(vp);console.log(`🎵 Audio: ${ad.toFixed(3)}s | Video: ${vd.toFixed(3)}s`);
  let v=vp;if(vd<ad-0.3){const lp=join(TMP,"looped.mp4"),r=runFFmpeg(["-y","-stream_loop","-1","-i",vp,"-t",ad.toFixed(3),"-c:v","libx264","-preset","fast","-crf","21","-pix_fmt","yuv420p","-an",lp]);if(r.status===0)v=lp;}
  const to=join(TMP,"merged_temp.mp4");runFFmpeg(["-y","-i",v,"-i",ap,"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","192k","-t",ad.toFixed(3),to]);
  applyMetadata(to,out);console.log(`✅ Done → ${out}`);
}

function linkFrame(s,d){if(!s)return;try{symlinkSync(s,d);}catch{copyFileSync(s,d);}}

// ═══════════════════════════════════════════════════════════════════════════
// MODE HANDLERS
// ═══════════════════════════════════════════════════════════════════════════

async function handleBgOnlyMode(){
  const cp=buildClipPlan(),fc=[],cd=[];console.log(`📊 ${cp.length} clips [${content_mode.toUpperCase()}]`);
  for(const c of cp){const{index:i,duration:d,videoPath:v,isHook:h}=c;process.stdout.write(`  [${i+1}/${cp.length}] ${d.toFixed(2)}s${h?" 🔥":""}... `);const bg=join(TMP,`bg_${String(i).padStart(3,"0")}.mp4`);processBackground(v,d,bg,i,h);fc.push(bg);cd.push(d);process.stdout.write("✓\n");}
  console.log(`\n✨ Concat ${fc.length} clips...`);const dv=xfadeConcat(fc,cd);
  console.log("🎵 Merging audio + metadata...");mergeAudio(dv,audio,outputPath);
  console.log(`\n🎉 BG Video [${content_mode.toUpperCase()}] → ${outputPath}\n`);
}

async function handleWordsOnlyMode(){
  const bv=videos[0];if(!bv){console.error("❌ words_only requires videos[0]");process.exit(1);}
  const w=buildWordList(),fsm=buildFrameStateMap(w),bm=buildSentenceBoundaryMap();
  const sec=detectVideoSections(),btp=getBigTransitionPoints(sec);
  console.log(`\n📊 Sections: ${sec.length}`);sec.forEach(s=>console.log(`   [${s.start.toFixed(2)}s] ${s.type.toUpperCase()} (${s.tag})`));
  console.log(`\n💥 Big transitions: ${btp.length}`);
  const{browser:br,page:pg}=await launchBrowser();
  try{
    console.log("\n🖼️  Rendering words PNGs [SHORT]...");const pc=await renderAllPNGsShort(pg,fsm,bm);console.log(`✅ ${pc.size} PNGs\n`);
    const fd=join(TMP,"frames_words");mkdirSync(fd,{recursive:true});const ep=pc.get("empty_n")||pc.get("intro_f0");
    for(let f=0;f<totalFrames;f++){const ts=bm[f]||null,k=stateKey(fsm[f],f,ts),s=pc.get(k)||ep,d=join(fd,`frame_${String(f).padStart(6,"0")}.png`);linkFrame(s,d);}
    const cm=join(TMP,"cap_words.mov");framesToMov(fd,cm);
    console.log("🔧 Overlaying words...");const wv=join(TMP,"with_words.mp4");overlayOnBg(bv,cm,audio,wv);
    const wt=join(TMP,"with_trans.mp4");await applyTransitionsToVideo(pg,wv,btp,wt);
    await br.close();
    console.log("\n📱 Applying iPhone 17 Pro Max metadata...");applyMetadata(wt,outputPath);
    console.log(`\n🎉 Final [SHORT] → ${outputPath}\n`);
  }finally{if(br.isConnected?.())await br.close();}
}

async function handleLongWordsOnlyMode(){
  const bv=videos[0];if(!bv){console.error("❌ long_words_only requires videos[0]");process.exit(1);}
  const w=buildWordList(),fsm=buildFrameStateMap(w),bm=buildSentenceBoundaryMap(),sm=buildSentenceMap();
  const sec=detectVideoSections(),btp=getBigTransitionPoints(sec);
  console.log(`\n📊 Sections: ${sec.length}`);sec.forEach(s=>console.log(`   [${s.start.toFixed(2)}s] ${s.type.toUpperCase()} (${s.tag})`));
  const{browser:br,page:pg}=await launchBrowser();
  try{
    console.log("\n🖼️  Rendering PNGs [LONG]...");const pc=await renderAllPNGsLong(pg,fsm,bm,sm);console.log(`✅ ${pc.size} PNGs [LONG]\n`);
    const fd=join(TMP,"frames_long");mkdirSync(fd,{recursive:true});const ep=pc.get("long_empty_n_hold_")||[...pc.values()][0];
    for(let f=0;f<totalFrames;f++){const ts=bm[f]||null,ws=fsm[f],sn=sm[f]||"",k=longStateKey(ws,ts,sn),s=pc.get(k)||ep,d=join(fd,`frame_${String(f).padStart(6,"0")}.png`);linkFrame(s,d);}
    const cm=join(TMP,"cap_long.mov");framesToMov(fd,cm);
    console.log("🔧 Overlaying words [LONG]...");const wv=join(TMP,"with_words_long.mp4");overlayOnBg(bv,cm,audio,wv);
    const wt=join(TMP,"with_trans_long.mp4");await applyTransitionsToVideo(pg,wv,btp,wt);
    await br.close();
    console.log("\n📱 Applying iPhone 17 Pro Max metadata...");applyMetadata(wt,outputPath);
    console.log(`\n🎉 Final [LONG] → ${outputPath}\n`);
  }finally{if(br.isConnected?.())await br.close();}
}

// ═══════════════════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════════════════

const MODE_HANDLERS={bg_only:handleBgOnlyMode,long_bg_only:handleBgOnlyMode,words_only:handleWordsOnlyMode,long_words_only:handleLongWordsOnlyMode};
async function main(){console.log(`\n🚀 Mode: ${mode} | Content: ${content_mode.toUpperCase()}\n`);const h=MODE_HANDLERS[mode];if(!h){console.error(`❌ Unknown mode: ${mode}`);process.exit(1);}await h();}
main().catch(e=>{console.error("❌",e);process.exit(1);});
