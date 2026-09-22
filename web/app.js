'use strict';
const $=id=>document.getElementById(id), token=window.STUDIO_TOKEN;
let libraryKind='article', selectionVersion=0, videoDirty=false, mediaBusy=false, jobsScope='all';
let current=null, platform='wechat_browser', settings={}, importing=false, polling=false;
const escapeHTML=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let toastTimer;
function toast(s){$('toast').textContent=s;$('toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').style.display='none',6000)}
async function api(path,data){const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-Studio-Token':token,...(data!==undefined?{'Content-Type':'application/json'}:{})},body:data===undefined?undefined:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw Error(result.error||'操作失败');return result}
let articleItems=[], jobItems=[], jobPage=0;
let articleOrder='newest';
try{articleOrder=localStorage.getItem('articleOrder')||'newest'}catch(e){}
if(!['newest','oldest','updated','title'].includes(articleOrder))articleOrder='newest';
$('articleSort').value=articleOrder;
const dateLabel=value=>value?new Date(typeof value==='number'?value*1000:value).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'时间未知';
function drawArticles(){
 const query=$('articleSearch').value.trim().toLocaleLowerCase();
 const items=articleItems.filter(x=>(x.title+' '+x.id).toLocaleLowerCase().includes(query)).sort((a,b)=>articleOrder==='title'?a.title.localeCompare(b.title,'zh-CN'):articleOrder==='oldest'?(a.created_at||0)-(b.created_at||0):articleOrder==='updated'?(b.updated_at||0)-(a.updated_at||0):(b.created_at||0)-(a.created_at||0));
 $('articleCount').textContent=items.length+(libraryKind==='video'?' 个视频':' 篇文章');$('articleList').replaceChildren();
 for(const item of items){const button=document.createElement('button');button.className='articleItem'+(current?.id===item.id?' active':'');button.setAttribute('aria-current',current?.id===item.id?'true':'false');button.title=item.title;button.innerHTML=(item.cover_url?'<img class="libraryThumb" src="'+escapeHTML(item.cover_url)+'" alt="">':'<span class="libraryPlaceholder" aria-hidden="true">'+(libraryKind==='video'?'▷':'▤')+'</span>')+'<strong>'+escapeHTML(item.title)+'</strong><small>'+escapeHTML(item.error||(libraryKind==='video'?(item.size?(item.size/1048576).toFixed(1)+' MB · MP4':'尚未添加视频'):item.images+' 张图片'))+'</small><time title="本地文章文件夹创建时间">创建于 '+escapeHTML(dateLabel(item.created_at))+'</time>';button.onclick=()=>selectArticle(item.id);$('articleList').append(button)}
 if(!items.length)$('articleList').textContent=query?'没有找到匹配的内容':(libraryKind==='video'?'还没有视频，导入一部作品吧。':'还没有文章，导入第一篇吧。');
 return items;
}
async function refreshList(){const kind=libraryKind;const data=await api(kind==='video'?'/api/videos':'/api/articles');if(kind!==libraryKind)return;articleItems=data;const items=drawArticles();if(!current&&items.length){await selectArticle(items[0].id,false);drawArticles()}}
$('articleSearch').oninput=drawArticles;
$('articleSort').onchange=()=>{articleOrder=$('articleSort').value;try{localStorage.setItem('articleOrder',articleOrder)}catch(e){}drawArticles()};
function allowLeave(){return !mediaBusy&&(!videoDirty||confirm('视频文案还未保存，放弃这次编辑并切换？'))}
async function selectArticle(id,refresh=true){if(!allowLeave())return;const request=++selectionVersion,kind=libraryKind;try{const data=await api((kind==='video'?'/api/videos/':'/api/articles/')+encodeURIComponent(id));if(request!==selectionVersion||kind!==libraryKind)return;current=data;videoDirty=false;$('workspace').hidden=false;$('empty').hidden=true;render();drawArticles();drawJobs();}catch(e){toast(e.message)}}
function render(){if(!current)return;if(libraryKind==='video'){renderVideo();return;}configureArticle();$('articleTitle').textContent=current.title;$('articleInfo').textContent=current.name+' · '+current.images.length+' 张图片 · 内容版本 '+current.fingerprint.slice(0,8);$('imageCount').textContent=current.images.length+' 张';$('images').replaceChildren();$('xhsGallery').replaceChildren();for(const [i,item] of (current.platform_image_items?.[platform==='xiaohongshu'?'xhs':platform]||current.image_items).entries()){const box=document.createElement('div');box.className='imageTile';const img=document.createElement('img');img.src=item.url;img.alt=item.path;const small=document.createElement('small');small.textContent=item.path;const index=document.createElement('b');index.textContent=item.cover?'封面':String(i+1).padStart(2,'0');const remove=document.createElement('button');remove.className='removeImage';remove.textContent='×';remove.title='从文章移除这张图片';remove.setAttribute('aria-label','移除图片 '+item.path);remove.onclick=async()=>{if(!confirm('从这篇文章中移除图片？正文和发布图片列表会同步移除；若为封面，将使用剩余第一张图。原图文件保留。'))return;try{current=await api('/api/articles/remove-image',{article_id:current.id,fingerprint:current.fingerprint,path:item.path,platform,confirmed:true});render();await refreshList()}catch(e){toast(e.message)}};box.append(img,index,small,remove);$('images').append(box);$('xhsGallery').append(img.cloneNode())}
 $('warnings').innerHTML=current.warnings.map(x=>'<p class="warning">'+escapeHTML(x)+'</p>').join('');
 $('wxPreview').srcdoc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font-family:Microsoft YaHei,sans-serif;margin:0;padding:25px;background:white;color:#24324a}h1{font-size:24px;line-height:1.6}p.byline{font-size:12px;color:#8894a6}img{max-width:100%}</style><h1>'+escapeHTML(current.title)+'</h1><p class="byline">'+escapeHTML(current.author||'公众号文章')+'</p>'+current.html+'</html>';
 $('xhsTitle').textContent=current.xhs_title;$('xhsBody').textContent=current.xhs_body;renderPlatform();}
function renderPlatform(){
 if(libraryKind==='video'){renderVideo();return;}
 const wx=platform.startsWith('wechat'),browser=platform==='wechat_browser',dv=platform==='douyin_video',dy=platform.startsWith('douyin');
 $('wxTab').classList.toggle('selected',wx);$('xhsTab').classList.toggle('selected',platform==='xiaohongshu');$('dyTab').classList.toggle('selected',dy&&!dv);$('dvTab').classList.toggle('selected',dv);
 $('wxPreview').hidden=!wx;$('xhsPreview').hidden=wx||dv;$('videoPreview').hidden=!dv;$('videoTools').hidden=!dv;$('images').hidden=dv;$('cardModes').hidden=platform!=='xiaohongshu';$('draft').hidden=!wx;$('loginXhs').hidden=platform!=='xiaohongshu';$('loginDy').hidden=!dy;$('generateCards').hidden=platform!=='xiaohongshu';
 $('sendTitle').textContent=wx?'发送到微信公众号':dv?'准备抖音视频':dy?'准备抖音图文':'准备小红书笔记';
 $('publish').textContent=browser?'自动填入公众号编辑器':wx?'正式发表到公众号':dv?'自动填入抖音视频编辑器':dy?'自动填入抖音编辑器':'自动填入小红书编辑器';
 $('loginWx').hidden=!browser;$('wxMode').hidden=!wx;$('draft').textContent=browser?'填入并尝试保存草稿':'只存到公众号草稿箱';
 $('accountLabel').textContent=wx&&!browser?(settings.has_secret?'官方接口 · 已配置':'官方接口 · 未配置'):dy?'抖音独立登录 · 复用已登录账号':'独立浏览器登录 · 核对账号后继续';
 $('sendInfo').textContent=wx&&!browser?'创建草稿并请求正式发表，不执行粉丝群发。':'自动上传本平台素材并填写文案，停在编辑器。请核对图片、话题和声明，再到平台手动发布。';
 $('platformContentHint').textContent=wx?'公众号使用长文及原始配图。':dv?(current?.douyin_video_custom?'使用独立视频文案。':'暂用图文短文，可以在上方修改并保存视频文案。'):dy?(current?.douyin_custom?'使用独立 douyin.md 文案。':'尚无 douyin.md，暂用小红书短文；发布前建议准备独立文案。'):'根据 xiaohongshu.md 短文生成 3:4 文字卡片；不改动公众号配图。教程请先在短文里写清每一步。';
 if(current){
 $('originalImages').classList.toggle('selected',current.xhs_image_mode==='original');$('cardImages').classList.toggle('selected',current.xhs_image_mode==='cards');
 $('cardImages').disabled=!current.xhs_card_images?.length;
 if(dv){const player=$('videoPlayer');if(player.getAttribute('src')!==(current.video_url||'')){if(current.video_url)player.src=current.video_url;else player.removeAttribute('src');player.load()}
 $('videoEmpty').hidden=!!current.video_url;player.hidden=!current.video_url;
 $('videoTitle').textContent=current.douyin_video_title;$('videoBody').textContent=current.douyin_video_body;
 $('videoTitleInput').value=current.douyin_video_title;$('videoBodyInput').value=current.douyin_video_body;}
 else $('videoPlayer').pause();
 const items=current.platform_image_items?.[dy?'douyin':'xhs']||current.image_items;$('imageCount').textContent=(wx?current.images.length:items.length)+' 张';if(!wx){$('xhsTitle').textContent=dy?current.douyin_title:current.xhs_title;$('xhsBody').textContent=dy?current.douyin_body:current.xhs_body}}
 if(dv)$('imageCount').textContent=current?.douyin_video?'1 个视频':'待上传';
 const errors=current?.checks[wx?'wechat':platform]||[];$('checks').innerHTML=errors.map(x=>'<p class="warning error">'+escapeHTML(x)+'</p>').join('');$('publish').disabled=errors.length>0;$('draft').disabled=errors.length>0;
}
async function send(action){if(!current)return;if(libraryKind==='video'){await prepareVideo();return;}const button=action==='draft'?$('draft'):$('publish');button.disabled=true;try{const result=await api('/api/publish',{article_id:current.id,fingerprint:current.fingerprint,platform,action});toast(result.resumed?'正在打开已有编辑器，保留当前编辑内容。':result.created?'任务已开始，请查看发送记录。':['manual_published','published','platform_confirmed'].includes(result.job?.status)?'这版内容已记录发布成功，不会重复填入。请选择新文章或更新内容。':'这个内容版本已有任务，请查看发送记录。');await refreshJobs()}catch(e){toast(e.message);if(e.message.includes('AppSecret'))openSettings()}finally{renderPlatform()}}
const labels={editor_ready:'已填入，待核对',running:'处理中',submitting:'正在提交',submitted:'平台处理中',draft_created:'已存草稿',published:'已发表',platform_confirmed:'平台提示成功',failed:'自动操作中断',manual_published:'已发布 · 手动确认',needs_login:'需要登录',uncertain:'结果待核实',rejected:'平台未发表',resolved_not_sent:'已核实未发送'};
async function refreshJobs(){jobItems=await api('/api/jobs');drawJobs()}
function drawJobs(){const filtered=jobsScope==='current'?jobItems.filter(j=>j.article===current?.id):jobItems;const pages=Math.max(1,Math.ceil(filtered.length/5));jobPage=Math.min(jobPage,pages-1);const jobs=filtered.slice(jobPage*5,jobPage*5+5);$('jobCount').textContent='共 '+filtered.length+' 条记录';$('jobsPage').textContent=(jobPage+1)+' / '+pages;$('jobsPrev').disabled=jobPage===0;$('jobsNext').disabled=jobPage>=pages-1;$('jobs').replaceChildren();if(!jobs.length){$('jobs').textContent='暂无发送记录。导入和预览不会发布内容。';return}for(const job of jobs){const row=document.createElement('div');row.className='job';const detail=document.createElement('div');detail.innerHTML='<strong>'+escapeHTML(job.article)+' · '+platformName(job.platform)+'</strong><p>'+escapeHTML(job.status==='failed'?'自动操作已停止；平台发布结果未同步。原始记录：'+job.message:job.message)+'</p><small>'+escapeHTML(dateLabel(job.created))+'</small>';const actions=document.createElement('div');actions.className='jobActions';const status=document.createElement('span');status.className='statusTag '+(['published','platform_confirmed','draft_created','manual_published'].includes(job.status)?'ok':['uncertain','failed','needs_login','rejected'].includes(job.status)?'warn':'');status.textContent=labels[job.status]||job.status;actions.append(status);if(!['running','submitting','submitted','uncertain'].includes(job.status)){const remove=document.createElement('button');remove.className='dangerText';remove.textContent='删除记录';remove.onclick=async()=>{if(!confirm('从列表删除这条记录？不会删除平台文章，系统仍保留防重复发布信息。'))return;try{await api('/api/jobs/delete',{id:job.id,confirmed:true});await refreshJobs()}catch(e){toast(e.message)}};actions.append(remove)}if(['failed','needs_login','uncertain','editor_ready','draft_created','resolved_not_sent'].includes(job.status)){const b=document.createElement('button');b.textContent='我已手动发布';b.onclick=async()=>{if(!confirm('确认这篇文章已在'+platformName(job.platform)+'完成发布？这只更新本地记录，不会再次发布文章。'))return;b.disabled=true;try{await api('/api/jobs/confirm-published',{id:job.id,confirmed:true});await refreshJobs();toast('已记录：手动发布成功。')}catch(e){b.disabled=false;toast(e.message)}};actions.append(b)}if(job.result.manual_confirmation){const audit=document.createElement('details');const summary=document.createElement('summary');summary.textContent='查看此前自动操作记录';const text=document.createElement('p');text.textContent=job.result.manual_confirmation.previous_message;audit.append(summary,text);detail.append(audit)}if(job.platform==='wechat'&&job.result.publish_id){const b=document.createElement('button');b.textContent='查询结果';b.onclick=async()=>{try{await api('/api/jobs/refresh',{id:job.id});await refreshJobs()}catch(e){toast(e.message)}};actions.append(b)}if(job.status==='uncertain'){const b=document.createElement('button');b.textContent='核实未发送';b.onclick=async()=>{if(!confirm('请先在平台后台核实。确定该记录没有创建草稿、没有提交、也没有发布？确认后才允许重新发送。'))return;try{await api('/api/jobs/resolve',{id:job.id,confirmed_not_sent:true});await refreshJobs()}catch(e){toast(e.message)}};actions.append(b)}for(const link of job.result.links||[]){if(!/^https?:\/\//.test(link))continue;const a=document.createElement('a');a.href=link;a.target='_blank';a.rel='noreferrer';a.textContent='查看文章';actions.append(a)}if(job.result.screenshot){const a=document.createElement('a');a.href=job.result.screenshot;a.target='_blank';a.textContent='现场截图';actions.append(a)}row.append(detail,actions);$('jobs').append(row)}}
function data64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file)})}
$('importButton').onclick=()=>{if(!allowLeave())return;(libraryKind==='video'?$('newVideoInput'):$('folderInput')).click()};$('folderInput').onchange=async e=>{if(importing)return;if(libraryKind!=='article')return;const selected=[...e.target.files];if(!selected.length)return;const name=selected[0].webkitRelativePath.split('/')[0];const files=selected.map(file=>({file,path:file.webkitRelativePath.split('/').slice(1).join('/')})).filter(x=>['article.md','xiaohongshu.md','douyin.md','douyin-video.md'].includes(x.path)||/^(images|image|xiaohongshu|douyin|douyin-video)\/.+\.(png|jpe?g|webp)$/i.test(x.path)||/^douyin-video\/.+\.mp4$/i.test(x.path));if(!files.some(f=>f.path==='article.md'))return toast('所选文件夹需要包含 article.md');if(files.length>80||files.reduce((n,x)=>n+x.file.size,0)>80*1024*1024)return toast('文件过多或合计超过 80 MB');importing=true;$('importButton').disabled=true;try{toast('正在读取文案和图片…');const payload=[];for(const x of files)payload.push({path:x.path,data:await data64(x.file)});current=await api('/api/import',{name,files:payload});$('workspace').hidden=false;$('empty').hidden=true;render();await refreshList();toast('文章已导入，原文件保留。请核对图文后发送。')}catch(e){toast(e.message)}finally{importing=false;$('importButton').disabled=false;e.target.value=''}};
async function openSettings(){try{settings=await api('/api/settings');$('appid').value=settings.appid;$('accountId').value=settings.account_id;$('secret').value='';$('secretState').textContent=settings.has_secret?'已保存密钥；留空不会清除。':'尚未保存 AppSecret。';$('settings').showModal()}catch(e){toast(e.message)}}
$('settingsOpen').onclick=openSettings;$('closeSettings').onclick=()=>$('settings').close();$('settingsForm').onsubmit=async e=>{e.preventDefault();try{settings=await api('/api/settings',{appid:$('appid').value,account_id:$('accountId').value,secret:$('secret').value});$('secret').value='';$('secretState').textContent=settings.has_secret?'密钥已保存在本机配置文件。':'未配置密钥';if(libraryKind!=='video')renderPlatform();toast('账号配置已保存')}catch(e){toast(e.message)}};
$('checkWechat').onclick=async()=>{const b=$('checkWechat');b.disabled=true;$('connectionResult').textContent='正在验证凭证和草稿读取权限…';try{const result=await api('/api/wechat/check',{});$('connectionResult').textContent=result.message}catch(e){$('connectionResult').textContent=e.message}finally{b.disabled=false}};
async function openXhs(){try{toast((await api('/api/xiaohongshu/login',{})).message)}catch(e){toast(e.message)}}
$('settingsLoginXhs').onclick=openXhs;$('loginXhs').onclick=openXhs;$('wxTab').onclick=()=>{platform=$('wxMode').value;render()};$('xhsTab').onclick=()=>{platform='xiaohongshu';render()};$('reloadArticle').onclick=()=>current&&selectArticle(current.id);$('refreshList').onclick=()=>refreshList().catch(e=>toast(e.message));$('publish').onclick=()=>send(platform==='wechat'?'publish':'prepare');$('draft').onclick=()=>send('draft');
async function poll(){if(polling||document.hidden)return;polling=true;try{await refreshJobs();const state=await api(platform==='bilibili'?'/api/videos/bilibili-status':platform==='wechat_browser'?'/api/wechat/browser-status':platform.startsWith('douyin')?'/api/douyin/login-status':'/api/login-status');$('loginStatus').textContent=state.status==='idle'?'':state.message}catch(e){/* Keep the last known state; never change a failure to success. */}finally{polling=false}}
(async()=>{try{settings=await api('/api/settings');await refreshList();await refreshJobs()}catch(e){toast(e.message)}})();setInterval(poll,3000);

async function openWx(){try{toast((await api('/api/wechat/browser-login',{})).message)}catch(e){toast(e.message)}}
$('loginWx').onclick=openWx;$('settingsLoginWx').onclick=openWx;$('wxMode').onchange=()=>{platform=$('wxMode').value;render()};

$('jobsPrev').onclick=()=>{jobPage=Math.max(0,jobPage-1);drawJobs()};
$('jobsNext').onclick=()=>{jobPage++;drawJobs()};

$('deleteArticle').onclick=async()=>{if(libraryKind==='video'){await deleteVideo();return;}if(!current||!confirm('将「'+current.title+'」移到本地回收目录？已发布内容、原始导入文件和发送记录不受影响。'))return;try{await api('/api/articles/delete',{article_id:current.id,fingerprint:current.fingerprint,confirmed:true});current=null;$('workspace').hidden=true;$('empty').hidden=false;await refreshList();toast('文章已移到本地回收目录。')}catch(e){toast(e.message)}};

$('dyTab').onclick=()=>{platform='douyin';render()};
$('loginDy').onclick=async()=>{try{toast((await api('/api/douyin/login',{})).message)}catch(e){toast(e.message)}};
$('generateCards').onclick=async()=>{if(!current)return;const button=$('generateCards');button.disabled=true;try{current=await api('/api/articles/cards',{article_id:current.id,fingerprint:current.fingerprint});render();toast('小红书文字卡片已生成，请逐页核对。')}catch(e){toast(e.message)}finally{button.disabled=false}};

async function switchImages(mode){if(!current)return;try{current=await api('/api/articles/xhs-mode',{article_id:current.id,fingerprint:current.fingerprint,mode});render();toast(mode==='cards'?'已切换为文字卡片':'已恢复原始配图')}catch(e){toast(e.message)}}
$('originalImages').onclick=()=>switchImages('original');$('cardImages').onclick=()=>switchImages('cards');
function platformName(p){return {wechat:'公众号',wechat_browser:'公众号',xiaohongshu:'小红书',douyin:'抖音图文',douyin_video:'抖音视频',bilibili:'B 站'}[p]||p}
function configureArticle(){
 document.body.classList.remove('videoMode');
 for(const id of ['wxTab','xhsTab','dyTab','assetPanel'])$(id).hidden=false;
 for(const id of ['dvTab','biliTab','renameVideo','loginBili'])$(id).hidden=true;
 $('deleteArticle').textContent='删除文章';$('previewLabel').textContent='阅读预览';
}
async function switchLibrary(kind){
 if(kind===libraryKind||!allowLeave())return;
 ++selectionVersion;libraryKind=kind;current=null;videoDirty=false;articleItems=[];
 platform=kind==='video'?'douyin_video':$('wxMode').value;
 $('videoPlayer').pause();$('videoPlayer').removeAttribute('src');$('videoPlayer').load();
 $('articleLibrary').classList.toggle('selected',kind==='article');$('videoLibrary').classList.toggle('selected',kind==='video');
 $('articleSearch').value='';$('articleSearch').placeholder=kind==='video'?'搜索视频作品…':'搜索文章…';
 $('libraryTitle').textContent=kind==='video'?'视频库':'文章库';
 $('importButton').textContent=kind==='video'?'＋ 导入 MP4 视频':'＋ 选择文章文件夹';
 $('libraryHelp').textContent=kind==='video'?'一部作品，多份平台文案。':'文字与配图，在这里有序成篇。';
 $('libraryFoot').textContent=kind==='video'?'视频独立保存在 videos/，原始文件保留。':'文章保存在 articles/，原始导入文件保留。';
 $('libraryHeadline').textContent=kind==='video'?'让作品，准备好登场。':'让创作，有条不紊。';
 $('librarySubtitle').textContent=kind==='video'?'预览视频、整理封面，为抖音和 B 站分别准备文案。':'整理图文、选择平台，核对后再发布。';
 $('emptyTitle').textContent=kind==='video'?'从一部视频开始':'从一篇文章开始';
 $('emptyDescription').textContent=kind==='video'?'导入 MP4，视频、封面与平台文案会独立管理。':'导入包含 article.md 和图片的文件夹，即可预览。';
 $('workspace').hidden=true;$('empty').hidden=false;drawArticles();drawJobs();
 try{await refreshList()}catch(e){toast(e.message)}
}
$('articleLibrary').onclick=()=>switchLibrary('article');$('videoLibrary').onclick=()=>switchLibrary('video');
function renderVideo(){
 if(!current)return;
 document.body.classList.add('videoMode');
 for(const id of ['wxTab','xhsTab','dyTab','wxPreview','xhsPreview','assetPanel','wxMode','loginWx','loginXhs','draft','generateCards','cardModes'])$(id).hidden=true;
 for(const id of ['dvTab','biliTab','videoPreview','videoTools','renameVideo'])$(id).hidden=false;
 const bili=platform==='bilibili',copy=current.copies[platform];
 $('dvTab').classList.toggle('selected',!bili);$('biliTab').classList.toggle('selected',bili);
 $('deleteArticle').textContent='删除视频';$('previewLabel').textContent='视频预览 · 原始比例';
 $('articleTitle').textContent=current.title;
 $('articleInfo').textContent=(current.size?(current.size/1048576).toFixed(1)+' MB · MP4':'待添加视频')+' · 创建于 '+dateLabel(current.created_at);
 const player=$('videoPlayer');
 if(player.getAttribute('src')!==(current.video_url||'')){player.pause();if(current.video_url)player.src=current.video_url;else player.removeAttribute('src');player.load()}
 player.hidden=!current.video_url;$('videoEmpty').hidden=!!current.video_url;
 if(current.cover_url)player.poster=current.cover_url;else player.removeAttribute('poster');
 $('videoCover').hidden=!current.cover_url;if(current.cover_url)$('videoCover').src=current.cover_url;
 $('videoTitle').textContent=copy.title;$('videoBody').textContent=copy.body||'在右侧填写本平台简介。';
 $('videoTitleInput').maxLength=bili?80:30;$('videoTitleInput').value=copy.title;$('videoBodyInput').value=copy.body;
 $('biliTags').value=current.bilibili_tags.join(', ');$('biliCategory').value=current.bilibili_category;
 $('biliCopyright').value=current.bilibili_copyright;$('biliSource').value=current.bilibili_source;
 $('biliFields').hidden=!bili;$('loginBili').hidden=!bili;$('loginDy').hidden=bili;
 $('copyHeading').textContent=(bili?'B 站':'抖音')+'发布文案';
 $('sendTitle').textContent=bili?'准备 B 站投稿':'准备抖音视频';
 $('accountLabel').textContent='独立浏览器登录 · 复用本机账号';
 $('publish').textContent=bili?'自动填入 B 站投稿页':'自动填入抖音视频编辑器';
 $('sendInfo').textContent=bili?'上传视频，填入标题、简介与标签。请在 B 站核对转码、封面、分区和声明，再手动投稿。':'上传视频并填入文案，停在编辑器。请在抖音核对封面、话题与声明后发布。';
 $('platformContentHint').textContent='切换平台可编辑不同文案，使用同一个视频文件。';
 $('previewFootnote').textContent='横屏和竖屏均按原始比例展示，不拉伸、不裁切。';
 $('checks').innerHTML=current.checks[platform].map(x=>'<p class="warning">'+escapeHTML(x)+'</p>').join('');
 $('publish').disabled=current.checks[platform].length>0||mediaBusy;
 videoDirty=false;updateDirty();
}
function updateDirty(){$('dirtyState').textContent=videoDirty?'有未保存修改':'已保存';$('dirtyState').classList.toggle('unsaved',videoDirty);$('titleLimit').textContent=$('videoTitleInput').value.length+' / '+(platform==='bilibili'?80:30)}
for(const id of ['videoTitleInput','videoBodyInput','biliTags','biliCategory','biliCopyright','biliSource'])$(id).addEventListener('input',()=>{videoDirty=true;updateDirty()});
function videoPlatform(p){if(!allowLeave())return;platform=p;videoDirty=false;renderVideo()}
$('dvTab').onclick=()=>videoPlatform('douyin_video');$('biliTab').onclick=()=>videoPlatform('bilibili');
$('videoPlayer').onloadedmetadata=()=>{const p=$('videoPlayer');$('videoMetrics').textContent=p.videoWidth+' × '+p.videoHeight+' · '+Math.floor(p.duration/60)+':'+String(Math.floor(p.duration%60)).padStart(2,'0')+' · '+(p.videoWidth>=p.videoHeight?'横屏':'竖屏')};
async function saveVideo(){
 if(!current||libraryKind!=='video'||mediaBusy)return false;
 const id=current.id,selectedPlatform=platform;
 try{const result=await api('/api/videos/text',{video_id:id,fingerprint:current.fingerprint,platform:selectedPlatform,title:$('videoTitleInput').value,body:$('videoBodyInput').value,tags:$('biliTags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean),category:$('biliCategory').value,copyright:$('biliCopyright').value,source:$('biliSource').value});
 if(current?.id===id&&platform===selectedPlatform){current=result;videoDirty=false;renderVideo()}toast('本平台文案已保存');return true
 }catch(e){toast(e.message);return false}
}
$('saveVideoText').onclick=saveVideo;
async function prepareVideo(){
 if(videoDirty&&!await saveVideo())return;
 const button=$('publish');button.disabled=true;
 try{const result=await api('/api/videos/prepare',{video_id:current.id,fingerprint:current.fingerprint,platform});
 toast(result.created?'正在打开平台并准备上传…':result.resumed?'正在切回已有编辑器。':'已有记录，请在发送记录核对状态。');await refreshJobs()}
 catch(e){toast(e.message)}finally{button.disabled=false}
}
function mediaLock(busy){
 mediaBusy=busy;
 for(const id of ['uploadVideo','uploadCover','importButton','articleLibrary','videoLibrary','saveVideoText','dvTab','biliTab','deleteArticle','renameVideo'])$(id).disabled=busy;
}
async function uploadMedia(file,cover=false){
 if(!current)return;
 if(videoDirty&&!await saveVideo())return;
 const id=current.id;
 if(!cover&&(!file.name.toLowerCase().endsWith('.mp4')||file.size>500*1048576))throw Error('请选择 500 MB 以内的 MP4');
 if(cover&&file.size>12*1048576)throw Error('封面请小于 12 MB');
 mediaLock(true);toast(cover?'正在导入封面…':'正在导入视频，请稍候…');
 try{
 const r=await fetch('/api/videos/'+encodeURIComponent(id)+(cover?'/cover':'/upload'),{method:'POST',headers:{'X-Studio-Token':token,'X-Content-Version':current.fingerprint,'Content-Type':cover?(file.type||'image/jpeg'):'video/mp4'},body:file});
 const data=await r.json();if(!r.ok)throw Error(data.error);
 if(current?.id===id){current=data;videoDirty=false;renderVideo()}await refreshList();toast(cover?'封面已保存；请在平台确认选用与裁剪。':'视频已导入，可以播放预览。');
 }finally{mediaLock(false);if(current)$('publish').disabled=current.checks[platform].length>0}
}
$('uploadVideo').onclick=()=>$('videoInput').click();$('uploadCover').onclick=()=>$('coverInput').click();
for(const [id,cover] of [['videoInput',false],['coverInput',true]])$(id).onchange=async e=>{try{if(e.target.files[0])await uploadMedia(e.target.files[0],cover)}catch(error){toast(error.message)}finally{e.target.value=''}};
$('newVideoInput').onchange=async e=>{
 const file=e.target.files[0];if(!file)return;
 try{
 if(!file.name.toLowerCase().endsWith('.mp4')||file.size>500*1048576)throw Error('请选择 500 MB 以内的 MP4');
 mediaLock(true);current=await api('/api/videos/create',{title:file.name.replace(/\.mp4$/i,'')});videoDirty=false;
 $('workspace').hidden=false;$('empty').hidden=true;renderVideo();await uploadMedia(file);
 }catch(error){toast(error.message)}finally{mediaLock(false);e.target.value=''}
};
async function deleteVideo(){
 if(!current||mediaBusy||!confirm('将「'+current.title+'」移入本地回收目录？平台作品与发布记录保留。'))return;
 try{await api('/api/videos/delete',{video_id:current.id,fingerprint:current.fingerprint,confirmed:true});current=null;videoDirty=false;$('videoPlayer').pause();$('videoPlayer').removeAttribute('src');$('videoPlayer').load();$('workspace').hidden=true;$('empty').hidden=false;await refreshList();toast('视频已移到本地回收目录。')}catch(e){toast(e.message)}
}
$('renameVideo').onclick=async()=>{if(!allowLeave())return;const title=prompt('视频作品名称',current.title);if(!title?.trim())return;try{current=await api('/api/videos/rename',{video_id:current.id,fingerprint:current.fingerprint,title});renderVideo();await refreshList()}catch(e){toast(e.message)}};
async function biliLogin(){try{toast((await api('/api/videos/bilibili-login',{})).message)}catch(e){toast(e.message)}}
$('loginBili').onclick=biliLogin;$('settingsLoginBili').onclick=biliLogin;
$('settingsLoginDy').onclick=()=>$('loginDy').click();
$('jobsAll').onclick=()=>{jobsScope='all';jobPage=0;$('jobsAll').classList.add('selected');$('jobsCurrent').classList.remove('selected');drawJobs()};
$('jobsCurrent').onclick=()=>{jobsScope='current';jobPage=0;$('jobsCurrent').classList.add('selected');$('jobsAll').classList.remove('selected');drawJobs()};
window.addEventListener('beforeunload',e=>{if(videoDirty||mediaBusy){e.preventDefault();e.returnValue=''}});
