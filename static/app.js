let currentRows = [];
let currentStock = null;
let currentPeriod = "1y";
let statusTimer = null;
const $ = id => document.getElementById(id);
const fmt = (n, digits=2) => n === null || n === undefined || !Number.isFinite(Number(n)) ? "—" : Number(n).toLocaleString("en-IN",{minimumFractionDigits:digits,maximumFractionDigits:digits});
const money = n => n === null || n === undefined || !Number.isFinite(Number(n)) ? "—" : "₹"+Number(n).toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2});
const pct = n => n === null || n === undefined || !Number.isFinite(Number(n)) ? "—" : (Number(n)>0?"+":"")+fmt(n)+"%";
const cap = n => { if(n==null || !Number.isFinite(Number(n))) return "—"; n=Number(n); if(n>=1e7)return "₹"+fmt(n/1e7,1)+" Cr"; if(n>=1e5)return "₹"+fmt(n/1e5,1)+" L"; return "₹"+fmt(n,0); };
function toast(msg){const el=$("toast");el.textContent=msg;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),3200)}
function statusClass(s){if(s==="Breakout after long gap")return "status-breakout";if(s==="At 52-week high")return "status-high";if(s==="Near 52-week high")return "status-near";if(s==="Error"||s==="No data")return "status-error";return "status-below"}
function statusBadge(s){return `<span class="status-badge ${statusClass(s)}">${s||"—"}</span>`}
function nav(tab){document.querySelectorAll(".nav-item").forEach(b=>b.classList.toggle("active",b.dataset.tab===tab));document.querySelectorAll(".tab-panel").forEach(p=>p.classList.toggle("active",p.id==="tab-"+tab));const titles={overview:"Market overview",breakouts:"Highs & breakouts",stock:"Stock intelligence",portfolio:"Investment tracker"};$("pageTitle").textContent=titles[tab];}
document.querySelectorAll(".nav-item").forEach(b=>b.addEventListener("click",()=>nav(b.dataset.tab)));
document.querySelectorAll("[data-goto]").forEach(b=>b.addEventListener("click",()=>nav(b.dataset.goto)));
function renderTable(rows, target, limit=null){
 const data=limit?rows.slice(0,limit):rows;
 if(!data.length){$(target).innerHTML='<div class="empty-state"><strong>No matching stocks</strong><p>Run a scan or change your filters.</p></div>';return}
 const headers=["Stock","Status","Price","52W high","Below high","Sector"];
 let html='<table class="data-table"><thead><tr>'+headers.map(h=>`<th>${h.toUpperCase()}</th>`).join("")+'</tr></thead><tbody>';
 data.forEach(r=>{html+=`<tr><td class="symbol-cell" data-symbol="${r.symbol}">${r.company_name||r.symbol}<div style="font-size:9px;color:#8c96aa;font-weight:500;margin-top:3px">${r.symbol}</div></td><td>${statusBadge(r.status)}</td><td>${money(r.price)}</td><td>${money(r.high_52w)}</td><td class="${r.below_52w_pct!=null&&r.below_52w_pct<1?'positive':''}">${pct(r.below_52w_pct)}</td><td>${r.sector||"—"}</td></tr>`});
 html+='</tbody></table>';$(target).innerHTML=html;
 $(target).querySelectorAll("[data-symbol]").forEach(el=>el.addEventListener("click",()=>{nav("stock");$("stockSymbol").value=el.dataset.symbol.replace(".NS","").replace(".BO","");loadStock(el.dataset.symbol,"1y")}));
}
async function refreshResults(){
 try{const res=await fetch("/api/results");const data=await res.json();currentRows=data.rows||[];updateMetrics();renderTable(currentRows.filter(r=>["Breakout after long gap","At 52-week high","Near 52-week high"].includes(r.status)),"overviewTable",8);filterTable()}catch(e){}
}
function updateMetrics(){
 const count=s=>currentRows.filter(r=>r.status===s).length;
 $("highMetric").textContent=count("At 52-week high");
 $("breakMetric").textContent=count("Breakout after long gap");
 $("nearMetric").textContent=currentRows.filter(r=>r.below_52w_pct!=null&&Number(r.below_52w_pct)<=5).length;
 // The navigation badge represents all rows shown by the Highs & breakouts page.
 $("navBreakoutCount").textContent=currentRows.filter(r=>["Breakout after long gap","At 52-week high","Near 52-week high"].includes(r.status)).length;
}
function filterTable(){const q=$("searchInput").value.toLowerCase();const status=$("statusFilter").value;const rows=currentRows.filter(r=>(status==="all"||r.status===status)&&[r.symbol,r.company_name,r.sector].some(v=>String(v||"").toLowerCase().includes(q)));$("resultCount").textContent=`${rows.length} stocks`;renderTable(rows,"fullTable")}
$("searchInput").addEventListener("input",filterTable);$("statusFilter").addEventListener("change",filterTable);
$("scanBtn").addEventListener("click",async()=>{try{const r=await fetch("/api/scan",{method:"POST"});const d=await r.json();if(!d.ok){toast(d.message);return}toast("Universe scan started");pollStatus()}catch(e){toast("Could not start scan")}}); 
function pollStatus(){if(statusTimer)clearInterval(statusTimer);statusTimer=setInterval(async()=>{try{const r=await fetch("/api/status");const s=await r.json();$("scanMessage").textContent=s.message;$("scanProgress").style.width=s.total?`${Math.min(100,s.completed/s.total*100)}%`:"0%";$("scanMeta").textContent=s.total?`${s.completed} / ${s.total} stocks processed${s.finished_at?" · Finished "+s.finished_at:""}`:"Scan runs in the background";if(!s.running){clearInterval(statusTimer);if(s.error)toast(s.error);else if(s.finished_at){await refreshResults();toast("Scan completed")}}}catch(e){}},1200)}
$("exportBtn").addEventListener("click",()=>window.location.href="/api/export");
$("historyTxtBtn").addEventListener("click",()=>window.location.href="/api/download/history.txt");
$("rollingCsvBtn").addEventListener("click",()=>window.location.href="/api/download/rolling.csv");
$("loadStockBtn").addEventListener("click",()=>loadStock($("stockSymbol").value,currentPeriod));
$("stockSymbol").addEventListener("keydown",e=>{if(e.key==="Enter")loadStock($("stockSymbol").value,currentPeriod)});
document.querySelectorAll("#timeframes button").forEach(b=>b.addEventListener("click",()=>{document.querySelectorAll("#timeframes button").forEach(x=>x.classList.remove("selected"));b.classList.add("selected");currentPeriod=b.dataset.period;if(currentStock)loadStock(currentStock.symbol,currentPeriod)}));
function renderFundamentals(f){
 const fields=[["Market cap",f.market_cap_cr!=null?`₹${fmt(f.market_cap_cr,0)} Cr`:null],["Sector",f.sector],["Industry",f.industry],["Trailing P/E",f.trailing_pe],["Forward P/E",f.forward_pe],["Price / book",f.price_to_book],["ROE",f.roe!=null?pct(f.roe):null],["Profit margin",f.profit_margin!=null?pct(f.profit_margin):null],["Operating margin",f.operating_margin!=null?pct(f.operating_margin):null],["Revenue growth",f.revenue_growth!=null?pct(f.revenue_growth):null],["Earnings growth",f.earnings_growth!=null?pct(f.earnings_growth):null],["Debt / equity",f.debt_to_equity],["Free cash flow",f.free_cashflow!=null?cap(f.free_cashflow):null],["Dividend yield",f.dividend_yield!=null?pct(f.dividend_yield):null],["Beta",f.beta]];
 $("fundamentalGrid").innerHTML=fields.map(([k,v])=>`<div class="fundamental-item"><div class="fundamental-label">${k}</div><div class="fundamental-value ${v==null||v===""?"missing":""}">${v==null||v===""?"Not available":typeof v==="number"?fmt(v):v}</div></div>`).join("");
 $("companySummary").textContent=f.summary||"No company description was returned by the data provider.";
}
function renderContext(stock){
 const s=stock.stats||{};const d=stock.data||[];const last=d[d.length-1]||{};const first=d[0]||{};
 const selectedReturn=(first.close!=null&&last.close!=null&&Number(first.close)!==0)?((Number(last.close)/Number(first.close)-1)*100):null;
 const periodLabels={"1d":"1D","1w":"1W","1mo":"1M","3mo":"3M","6mo":"6M","1y":"1Y","3y":"3Y","5y":"5Y","max":"MAX"};
 $("stockReturn").textContent=`${periodLabels[stock.period]||currentPeriod.toUpperCase()} return: ${pct(selectedReturn)}`;
 const items=[["52-week high",money(s.high_52w)],["Distance from high",pct(s.below_52w_pct!=null?-s.below_52w_pct:null)],["Current status",s.status||"—"],["Prior breakout",s.previous_breakout_date||"Not found"],["Days since prior breakout",s.days_since_previous_breakout!=null?`${s.days_since_previous_breakout} calendar days`:"Not available"],["Latest data date",s.last_date||"—"]];
 $("breakoutContext").innerHTML=items.map(([k,v])=>`<div class="context-item"><div class="context-label">${k}</div><div class="context-value">${v}</div></div>`).join("");
 $("stockPrice").textContent=money(last.close);$("stockDistance").textContent=s.below_52w_pct!=null?`${fmt(s.below_52w_pct)}% below 52W high`:"52-week high unavailable";
 $("sHigh").textContent=money(s.high_52w);$("sDistance").textContent=s.below_52w_pct!=null?`${fmt(s.below_52w_pct)}% below high`:"—";
 [["sEma20",last.ema20,"sEma20State"],["sEma50",last.ema50,"sEma50State"],["sEma200",last.ema200,"sEma200State"]].forEach(([id,v,state])=>{$(id).textContent=money(v);$(state).textContent=last.close!=null&&v!=null?(last.close>=v?"Price above EMA":"Price below EMA"):"—";$(state).className="metric-foot "+(last.close!=null&&v!=null?(last.close>=v?"positive":"negative"):"")});
 $("sRsi").textContent=last.rsi!=null?fmt(last.rsi,1):"—";
}
function renderChart(stock){
 const d=stock.data||[];if(!d.length)return;
 const x=d.map(r=>r.date),retKeys=["1W","1M","3M","6M","12M","3Y","5Y","Since available"];
 const custom=d.map(r=>retKeys.map(k=>r.returns[k]));
 const price={x,y:d.map(r=>r.close),type:"scatter",mode:"lines",name:"Price",line:{color:"#263b65",width:2},customdata:custom,hovertemplate:"<b>%{x}</b><br>Price: ₹%{y:,.2f}<br><br>Returns as of this date:<br>1W: %{customdata[0]:.2f}%<br>1M: %{customdata[1]:.2f}%<br>3M: %{customdata[2]:.2f}%<br>6M: %{customdata[3]:.2f}%<br>12M: %{customdata[4]:.2f}%<br>3Y: %{customdata[5]:.2f}%<br>5Y: %{customdata[6]:.2f}%<br>Since available: %{customdata[7]:.2f}%<extra></extra>"};
 const ema=(key,name,color)=>({x,y:d.map(r=>r[key]),type:"scatter",mode:"lines",name,line:{color,width:1.25},hovertemplate:`${name}: ₹%{y:,.2f}<extra></extra>`});
 const rsiTrace={x,y:d.map(r=>r.rsi),xaxis:"x",yaxis:"y2",type:"scatter",mode:"lines",name:"RSI (14)",line:{color:"#1da887",width:1.5},hovertemplate:"RSI: %{y:.1f}<extra></extra>"};
 const traces=[price,ema("ema20","EMA 20","#f0a544"),ema("ema50","EMA 50","#5876f0"),ema("ema200","EMA 200","#9c75d9"),rsiTrace];
 const layout={paper_bgcolor:"#fff",plot_bgcolor:"#fff",margin:{l:55,r:25,t:12,b:35},font:{family:"DM Sans, sans-serif",size:11,color:"#68758d"},hovermode:"x unified",hoverlabel:{font:{family:"DM Sans, sans-serif",size:13,color:"#182238"},bgcolor:"#ffffff",bordercolor:"#cfd7e8",namelength:-1},showlegend:false,xaxis:{domain:[0,1],anchor:"y",gridcolor:"#f0f2f7",linecolor:"#e6eaf2",rangeslider:{visible:false},tickfont:{size:10}},yaxis:{domain:[.28,1],gridcolor:"#edf0f6",zeroline:false,tickprefix:"₹",tickformat:",.0f"},yaxis2:{domain:[0,.19],range:[0,100],gridcolor:"#f0f2f7",zeroline:false,tickvals:[30,50,70],title:{text:"RSI",font:{size:10}},tickfont:{size:10}},shapes:[{type:"line",xref:"paper",x0:0,x1:1,yref:"y2",y0:70,y1:70,line:{color:"#e6a2a9",width:1,dash:"dot"}},{type:"line",xref:"paper",x0:0,x1:1,yref:"y2",y0:30,y1:30,line:{color:"#8acdb9",width:1,dash:"dot"}}],annotations:[{xref:"paper",yref:"paper",x:0,y:.205,text:"RSI (14)",showarrow:false,font:{size:9,color:"#1da887"},xanchor:"left"}]};
 Plotly.newPlot("priceChart",traces,layout,{responsive:true,displaylogo:false,modeBarButtonsToRemove:["lasso2d","select2d","autoScale2d"]});
}
async function loadStock(symbol,period="1y"){
 if(!symbol||!String(symbol).trim()){toast("Enter a stock symbol");return}
 currentStock={symbol:String(symbol).trim()};currentPeriod=period;
 $("stockEmpty").classList.add("hidden");$("stockContent").classList.remove("hidden");$("stockName").textContent="Loading stock data…";$("stockTicker").textContent=String(symbol).toUpperCase();
 try{
  const res=await fetch(`/api/stock/${encodeURIComponent(symbol)}?period=${period}`);const stock=await res.json();
  if(!res.ok||!stock.ok)throw new Error(stock.message||"Could not load stock");
  currentStock=stock;$("stockName").textContent=stock.company_name||stock.symbol;$("stockTicker").textContent=stock.symbol;
  $("stockSector").textContent=[stock.fundamentals?.sector,stock.fundamentals?.industry].filter(Boolean).join(" · ")||"STOCK PROFILE";
  renderFundamentals(stock.fundamentals||{});renderContext(stock);renderChart(stock);const chartSub=document.querySelector(".chart-head .panel-head p, .chart-head p");if(chartSub){chartSub.textContent=(period==="1d"||period==="1w")?"Intraday price view · hover for price and daily holding-period returns":"Hover the chart for price, indicators and historical holding-period returns"}nav("stock");
 }catch(e){toast(e.message);$("stockName").textContent="Could not load stock data"}
}

function portfolioMoney(n){return n==null?"—":money(n)}
function renderPortfolioTable(rows){
 const target=$("portfolioTable");
 if(!rows.length){target.innerHTML='<div class="empty-state"><strong>No investments recorded</strong><p>Add your first investment above to start tracking.</p></div>';return}
 let html='<table class="data-table"><thead><tr><th>STOCK</th><th>QUANTITY</th><th>BUY PRICE</th><th>BUY DATE</th><th>LAST PRICE</th><th>INVESTED</th><th>CURRENT VALUE</th><th>P/L</th><th>RETURN</th><th>ACTION</th></tr></thead><tbody>';
 rows.forEach(r=>{
  const pnlClass=r.pnl==null?"":(r.pnl>=0?"positive":"negative");
  html+=`<tr><td class="symbol-cell">${r.symbol}<div style="font-size:10px;color:#8c96aa;margin-top:3px">${r.notes||""}</div></td><td>${r.quantity}</td><td>${money(r.buy_price)}</td><td>${r.buy_date}</td><td>${money(r.current_price)}</td><td>${money(r.invested)}</td><td>${money(r.current_value)}</td><td class="${pnlClass}">${r.pnl==null?"—":money(r.pnl)}</td><td class="${pnlClass}">${pct(r.return_pct)}</td><td><button class="mini-action" data-delete-holding="${r.id}">Remove</button></td></tr>`;
 });
 html+='</tbody></table>';target.innerHTML=html;
 target.querySelectorAll("[data-delete-holding]").forEach(btn=>btn.addEventListener("click",async()=>{
  if(!confirm("Remove this investment record?"))return;
  const res=await fetch(`/api/portfolio/${btn.dataset.deleteHolding}`,{method:"DELETE"});const d=await res.json();
  if(d.ok){toast("Investment record removed");refreshPortfolio()}else toast(d.message||"Could not remove record");
 }));
 $("portfolioCount").textContent=`${rows.length} holding${rows.length===1?"":"s"}`;
}
async function refreshPortfolio(){
 const target=$("portfolioTable");target.style.opacity=".65";
 try{
  const res=await fetch("/api/portfolio/valuation");const d=await res.json();
  if(!res.ok||!d.ok)throw new Error(d.message||"Could not load portfolio");
  $("portfolioInvested").textContent=money(d.total_invested);
  $("portfolioValue").textContent=money(d.current_value);
  $("portfolioPnl").textContent=money(d.pnl);
  $("portfolioPnl").className="metric-value "+(d.pnl>=0?"positive":"negative");
  $("portfolioReturn").textContent=`Return: ${pct(d.return_pct)}${d.unpriced_count?` · ${d.unpriced_count} unpriced`:""}`;
  renderPortfolioTable(d.holdings||[]);
 }catch(e){toast("Portfolio refresh failed: "+e.message)}finally{target.style.opacity="1"}
}
function updateInvestmentCost(){
 const q=Number($("investmentQuantity").value||0),p=Number($("investmentBuyPrice").value||0);
 $("investmentCost").textContent=money(q*p);
}
$("investmentQuantity").addEventListener("input",updateInvestmentCost);
$("investmentBuyPrice").addEventListener("input",updateInvestmentCost);
$("investmentBuyDate").value=new Date().toISOString().slice(0,10);
$("investmentForm").addEventListener("submit",async e=>{
 e.preventDefault();
 const payload={symbol:$("investmentSymbol").value,quantity:$("investmentQuantity").value,buy_price:$("investmentBuyPrice").value,buy_date:$("investmentBuyDate").value,notes:$("investmentNotes").value};
 try{const res=await fetch("/api/portfolio",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const d=await res.json();if(!res.ok||!d.ok)throw new Error(d.message||"Could not save investment");toast(d.message);$("investmentForm").reset();$("investmentBuyDate").value=new Date().toISOString().slice(0,10);updateInvestmentCost();refreshPortfolio()}catch(err){toast(err.message)}
});
$("refreshPortfolioBtn").addEventListener("click",()=>refreshPortfolio());
$("buySetupForm").addEventListener("submit",async e=>{
 e.preventDefault();const symbol=$("buySetupSymbol").value.trim();if(!symbol)return;
 const box=$("buySetupResult");box.innerHTML='<div class="empty-state"><strong>Analysing…</strong><p>Loading latest price history and available fundamentals.</p></div>';
 try{
  const res=await fetch(`/api/buy-setup/${encodeURIComponent(symbol)}`);const d=await res.json();if(!res.ok||!d.ok)throw new Error(d.message||"Could not analyse setup");
  const tech=d.technical_notes||[],fund=d.fundamental_notes||[];
  box.innerHTML=`<div class="setup-price"><div><span>Last available close</span><strong>${money(d.price)}</strong></div><div><span>Technical alignment</span><strong>${d.technical_score}/3 EMAs</strong></div><div><span>RSI (14)</span><strong>${fmt(d.rsi,1)}</strong></div></div>
  <div class="setup-section"><h4>Technical checklist</h4><ul>${tech.map(x=>`<li>${x}</li>`).join("")}</ul></div>
  <div class="setup-section"><h4>Fundamental review · ${d.fundamental_label}</h4><ul>${fund.map(x=>`<li>${x}</li>`).join("")}</ul></div>
  <div class="entry-watch"><strong>Potential observation zone</strong><p>${d.entry_note}</p><small>EMA 20: ${money(d.ema20)} · EMA 50: ${money(d.ema50)} · EMA 200: ${money(d.ema200)}</small></div>
  <p class="setup-warning">${d.warning}</p>`;
 }catch(err){box.innerHTML=`<div class="empty-state"><strong>Analysis unavailable</strong><p>${err.message}</p></div>`}
});

async function init(){await refreshResults();try{const r=await fetch("/api/status");const s=await r.json();$("scanMessage").textContent=s.message;if(s.running)pollStatus();if(s.total){$("scanProgress").style.width=`${s.completed/s.total*100}%`;$("scanMeta").textContent=`${s.completed} / ${s.total} stocks processed`}}catch(e){}}
init();
refreshPortfolio();
