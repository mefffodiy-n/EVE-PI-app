// По умолчанию API — на том же origin, что и страница: так работает и
// `python run.py` (Flask отдаёт и страницу, и API с 8000), и прод за nginx
// (любой домен). Абсолютный адрес Flask нужен только для Live Server —
// тогда страница открыта с другого локального порта (не 8000), а API
// всё равно поднят на 127.0.0.1:8000 (`PI_DEV_CORS=1 python run.py`).
// Раньше проверялось наоборот («не равно ровно 127.0.0.1:8000/localhost:8000
// — значит Live Server»), и с любого настоящего домена в проде страница
// пыталась достучаться до localhost:8000 браузера пользователя.
const FLASK='http://127.0.0.1:8000';
const LIVE_SERVER=/^(127\.0\.0\.1|localhost)$/.test(location.hostname)
  && location.port && location.port!=='8000';
const API=LIVE_SERVER?`${FLASK}/api`:'/api';
// Язык ответа сервера (тексты ошибок API — правило 9): Accept-Language на каждый запрос к API.
const _origFetch=window.fetch.bind(window);
window.fetch=(url,opts={})=>{
  if(String(url).startsWith(API)){
    const h=new Headers(opts.headers||{});
    if(!h.has('Accept-Language')) h.set('Accept-Language',lang);
    opts={...opts,headers:h};
  }
  return _origFetch(url,opts);
};
const ICON=(id,size)=>`https://images.evetech.net/types/${id}/icon?size=${size||64}`;
// Портрет персонажа — тот же публичный CDN CCP, без токена. Только для
// реальных ESI-персонажей: у dev-заглушек этого character_id не
// существует в игре, картинка получает 404 и удаляет себя (onerror),
// снизу остаются инициалы — как было единственным видом до Фазы 3.
const PORTRAIT=id=>`https://images.evetech.net/characters/${id}/portrait?size=64`;
const charIdOf=name=>{ const c=crew.find(x=>x.name===name); return c?c.character_id:null; };
const avatarImg=characterId=>characterId
  ? `<img src="${PORTRAIT(characterId)}" alt="" onerror="this.remove()">` : '';

let ids={}, recipeInputs={}, schematics={}, typeVolumes={}, plan=[], staffing={}, view='details', sortBy='load';
let productTiers={}, activeTiers=new Set();
const PRODUCT_TIER_ORDER=['P2','P3','P4'];
let lang='ru', crew=[], meta={};
let regions={}, systemCounts={}, allProducts=[], allConstellations=[];
let isAdmin=false;  // /api/admin/ping — см. checkAdminAccess()
let dataCounts=null;  // {bases, products} — для чипа data, перерисовывается при смене языка

function renderDataChip(){
  const el=document.getElementById('dataStatus'); if(!el) return;
  if(dataCounts==='failed'){ el.textContent=t('dataFailed'); return; }
  if(!dataCounts) return;
  el.textContent=`${pl(dataCounts.bases,'pConst')} · ${pl(dataCounts.products,'pProd')}`;
}
const chosen={products:new Set(), constellations:new Set()};

/* ── Двуязычие ────────────────────────────────────────────────────
   Настройки живут на сервере? Нет: язык и тема — это предпочтения
   отображения, они принадлежат браузеру и хранятся локально.
   Персонажи, наоборот, живут на сервере — поэтому не пропадают при
   перезаходе и не зависят от очистки кэша. */
const T={
 ru:{brandSub:'by evecraft',tabDash:'Дашборд',tabSetup:'Настройки',
  chipData:'данные',help:'Справка',about:'О проекте',sso:'Войти через EVE SSO',
  online:'в игре',offline:'нет связи',noData:'нет данных',
  planBrief:'Задание на план',planBriefSub:'три шага — что, откуда, куда',
  whatProduce:'Что производим',targets:'Целевые продукты',whereMine:'Где добываем',
  constellations:'Созвездия',margin:'Запас на истощение месторождений',
  whereProcess:'Где перерабатываем',homeSystem:'Домашняя система',crew:'Персонажи',
  build:'Построить план',noPlan:'План не построен. Откройте вкладку «Настройки».',
  colonies:'колоний',mining:'добыча',processing:'переработка',
  details:'Детали',back:'Назад',reserveOk:'запас везде есть',
  noReserve:'без запаса',sortLoad:'По загрузке',sortReserve:'По остатку запаса',
  sortPilot:'По имени персонажа',sortSystem:'По системе',
  cProc:'переработка',cMine:'добыча',addCharacter:'Добавить персонажа',adminPanel:'Admin-панель',
  unlink:'Отвязать',unlinkHint:'Стереть токен на нашей стороне — планировщик перестанет видеть персонажа до повторного входа. Доступ на стороне CCP отдельно отзывается на eveonline.com/account/third_party_apps.',
  reconnect:'Перезайти',reconnectHint:'Доступ ESI для этого персонажа истёк или отозван (сменён пароль, отозвано приложение) — колонии и скиллы больше не обновляются. Перезайдите через EVE SSO этим же персонажем.',
  unlinkFail:'Не удалось отвязать персонажа',
  groupHint:'Сделать этого персонажа основным для группы — на новом устройстве или в другом браузере вход любым из группы подтянет всех остальных к нему.',
  groupSave:'Сделать основным',groupSaved:'Теперь основной',groupSaveFail:'Не удалось назначить основного',
  groupPrimary:'основной',
  pocoProfitTitle:'Прогноз прибыльности плана',
  pocoRevenue:'Выручка / мес',pocoTax:'Налог POCO / мес',pocoPurchaseCost:'Закупка P1 / мес',pocoNetProfit:'Чистая прибыль / мес',
  pocoMissingPrices:'Нет цены: {list}',pocoMissingRates:'Нет данных о ставке POCO: {list}',
  pocoMissingVolumes:'Нет данных об объёме единицы (пропускная способность причала не учтена): {list}',
  pocoLogisticsBottleneck:'Цепочка упирается в пропускную способность причала — реальная доля рабочего времени:',
  pocoHigherThanBest:'Ставка POCO здесь выше, чем на других перерабатывающих колониях плана',
  pocoIncomplete:'Чистая прибыль не считается, пока не известны все ставки и не собраны все цены — частичная сумма выглядела бы точнее, чем есть на самом деле.',
  pocoAssumption:'Оценка на {hours} часов в месяце, без истощения месторождений. Простой из-за пропускной способности причала уже учтён (см. выше), прочие простои — нет. Налог — и на вывоз, и на ввоз на каждом перемещении между планетами по цепочке.',
  pocoRevenueByProductTitle:'Выручка по продуктам',pocoNotesTitle:'Допущения и предупреждения',
  pocoRevenueByProductQty:'Ед. / мес',pocoRevenueByProductPrice:'Цена, ISK',pocoRevenueByProductRevenue:'Выручка / мес',
  purchaseListTitle:'Список закупки сырья (P1)',
  purchaseListProduct:'Продукт',purchaseListQty:'Кол-во / мес',purchaseListPrice:'Цена, ISK',
  purchaseListCost:'Стоимость / мес',purchaseListTotal:'Итого',purchaseListNoPrice:'нет цены',
  purchaseListAsOf:'Цены на момент построения плана{stamp}',purchaseListNoSnapshot:' (снимок цен не найден)',
  coloniesProfitTitle:'Прогноз прибыльности по факту',
  coloniesProfitMissingOutput:'Неизвестна текущая скорость: {list}',
  coloniesProfitAssumption:'По реальной текущей скорости каждой колонии (с поправкой на затухание добычи и простой фабрик), не по теоретическому максимуму. Налог — только на вывоз с самой планеты, без учёта того, куда сырьё едет дальше между разными колониями: этой связи ESI не отдаёт.',
  chipJobs:'сборщики',jobsOk:'ок',jobsStale:'застряли',jobsFailing:'падают',
  jobsNever:'ни разу не запускался',jobsFailingCount:'падает {n}-й раз подряд',
  jobServerStatus:'Статус сервера',jobMarketPrices:'Рыночные цены',
  jobRefreshTokens:'Обновление токенов ESI',jobSyncSkills:'Скиллы персонажей',
  jobSyncColonies:'Статус колоний',jobBackup:'Резервная копия',jobRefreshSde:'Скелет планет из SDE',
  unlinkedRevoked:'Персонаж отвязан, доступ отозван и на стороне CCP',
  unlinkedLocalOnly:'Персонаж отвязан на нашей стороне (секрет приложения не задан — настоящий отзыв недоступен)',
  planetSlots:'слотов планет',purchaseP1:'Покупать весь P1 на бирже (не строить добычу)',wholeRegion:'Весь регион',clearAll:'Снять всё',nothingChosen:'ничего не выбрано',systems:'систем',selectAll:'Выбрать все',pickProducts:'Выберите хотя бы один продукт',pickConst:'Выберите хотя бы одно созвездие',export:'Экспорт в Excel',jumpPlan:'↑ План',jumpColonies:'↓ Мои колонии',jumpShopping:'↓ Список закупки',shopping:'Список закупки: командные центры',shopTotal:'Всего командных центров',exportEmpty:'Нечего экспортировать: нет ни плана, ни колоний',exportFail:'Не удалось выгрузить файл',profitTitle:'Что выгоднее производить',collectedAgo:'снимок собран',minutesAgo:'мин назад',stale:'устарел',colProduct:'Продукт',colColonies:'Планет',colPerColony:'ISK / колония-час',colPerHour:'ISK / час',colPrice:'Цена за единицу',noPrice:'нет цены',noSnapshot:'Снимок цен не собран',advDeficit:'Персонажей не хватает на эту цепочку',advSurplus:'Персонажей больше, чем нужно',advDeficitLead:'Полный цикл требует {need} колоний, а в пуле {have} слотов. Вот что поместится целиком:',advSurplusLead:'Выбранное занимает {need} колоний из {have}. Свободно {spare} — можно добавить:',advTake:'Взять',advIgnoreDeficit:'Строить как есть',advIgnoreSurplus:'Занять свободных добычей',advIgnoreDeficitHint:'план будет с дефицитом сырья',advIgnoreSurplusHint:'добыча сырья той же цепочки, по убыванию дефицитности',advDuplicateChain:'Можно продублировать всю цепочку ещё {n} раз(а)',linesPerTarget:'Линий на цепочку',colonies3:'колоний',excess:'Добыча (избыток)',mSpread:'Разброс по системам',logi:'всего задействовано',systemsAvg:'систем на персонажа',sysOne:'система',sysFew:'системы',sysMany:'систем',logiGood:'колонии собраны компактно',logiPoor:'урожай придётся собирать в разъездах',mColonies:'Колоний в плане',mChars:'Персонажей занято',mPeak:'Самая нагруженная',mShort:'Не хватает персонажей',planClosed:'план закрыт',fitsUpTo:'двойной шаблон помещается на планеты до',km:'км',suitableHere:'подходящих планет в системе',ofThem:'из них',decisionTitle:'План упёрся в размер планет',decisionText:'В выбранной домашней системе двойной шаблон не помещается ни на одну планету. Выберите, как поступить.',chooseOther:'Выбрать другую систему',useSingle:'Ставить по одному шаблону',singleNote:'планет и персонажей потребуется вдвое больше',savedPlans:'Сохранённые планы',save:'Сохранить',load:'Открыть',del:'Удалить',compare:'Сравнить',noSaved:'Пока ничего не сохранено',saveFirst:'Сначала постройте план',pickTwo:'Отметьте два плана для сравнения',cmpTitle:'Разница',colonies2:'колоний',chars2:'персонажей',peak2:'пиковая загрузка',onlyLeft:'Только в первом',onlyRight:'Только во втором',same:'Совпало',
  noPlanLong:'План не построен. Откройте вкладку «Настройки», выберите продукты и постройте план.',
  planEmpty:'План пуст.',searchPh:'поиск…',planNamePh:'название плана',
  crewPre:'Появятся после расчёта: планировщик сам распределяет персонажей по планетам с учётом их прокачки.',
  dataFailed:'не загружены',snapshotMade:'Снимок сделан',
  profitNote:'Ранжировано по ISK на колонию в час: ограниченный ресурс — планеты и персонажи. Налог POCO, доставка и биржевые сборы не учтены, поэтому числа — верхняя оценка.',
  noSnapshotHint:'Соберите его: python -m scripts.refresh_market_prices — или планировщик python -m scripts.scheduler, который делает это по расписанию.',
  pgFrom:'Из чего сложилось (Power)',sStruct:'структуры',sHeads:'головы',sLinks:'линки',
  capCap:'Вместимость',capContent:'Содержимое',cycNow:'Текущий цикл',cycNone:'данных о цикле нет',
  extractsW:'Добывает',heads10:'10 голов',cycUnknown:'Состояние цикла неизвестно',
  extrRateNow:'Сейчас',extrRateAvg:'В среднем',extrRateUnit:'ед./ч',
  extrHistoryBuilding:'копим историю',
  extrDeficit:'дефицит — нужна дополнительная добыча',
  extrDeficitChain:'дефицит — под угрозой цепочка {product}, добавьте ещё добывающую колонию',
  afterGame:'появится после подключения к игре',producesW:'Производит',inputW:'Вход',
  pHead:['голова','головы','голов'],emptyStorage:'пусто',
  inProduction:'В производстве',cycIdleSince:'Простаивает',
  cycProjectedHint:'подтверждено на момент последнего захода в колонию, дальше — неизвестно',
  cycNeverStarted:'Ещё ни разу не запускалась',
  sharedExtract:'На этой планете {n} наших экстрактора на одном сырье — выработка каждого будет ниже расчётной.',
  onPlan:'к плану',onPlanets:'на',
  thColony:'Колония',thColonySub:'кто и где',thProd:'Производство',thProdSub:'что делает и из чего',
  thLoad:'Нагрузка',thLoadSub:'из чего сложилась и сколько осталось',thChar:'Персонаж',thPlanet:'Планета',
  thProduct:'Продукт',thPgFrom:'Из чего сложилось (PG)',thReserve:'Запас',
  pColony:['колония','колонии','колоний'],pPlanet:['планете','планетах','планетах'],
  pNote:['замечание','замечания','замечаний'],pConst:['созвездие','созвездия','созвездий'],
  pProd:['продукт','продукта','продуктов'],
  ssoUnavailable:'Вход недоступен',
  notJson:'{api} вернул не JSON. Запустите «python run.py» и откройте {flask}/',
  pPlanetN:['планета','планеты','планет'],coloniesIdle:'простаивает',coloniesAttention:'требуют внимания',
  coloniesDeficit:'дефицит добычи',
  launchpadFull:'причал заполнен',needsAttention:'Требует внимания',freeSlot:'свободный слот планеты',
  themeTitle:'Тема',moreTitle:'Ещё',viewList:'Список по персонажам',viewGrid:'Сетка',sortTitle:'Сортировка',loadLbl:'Загрузка',
  inGameColonies:'Мои колонии в игре',cycOver:'программа завершена',cycIdle:'программа не запущена',
  hUnit:'ч',minUnit:'мин',dUnit:'д',pPin:['пин','пина','пинов'],
  progEnds:'до конца программы экстрактора',syncedColony:'колония в игре',
  procCycleLbl:'до конца цикла переработки',
  procDepletionLbl:'до конца запаса сырья в причале',
  cycNoFactory:'ESI не отдаёт состояние фабрик — только экстракторы',
  role_mine:'Добыча',role_mine_surplus:'Добыча (избыток)',role_proc:'Переработка',role_direct_p2:'Прямое P2',
  factW:'фабрик',pFactory:['фабрика','фабрики','фабрик']},
 en:{brandSub:'by evecraft',tabDash:'Dashboard',tabSetup:'Settings',
  chipData:'data',help:'Help',about:'About',sso:'Log in with EVE SSO',
  online:'online',offline:'offline',noData:'no data',
  planBrief:'Plan brief',planBriefSub:'three steps — what, where from, where to',
  whatProduce:'What to produce',targets:'Target products',whereMine:'Where to extract',
  constellations:'Constellations',margin:'Depletion safety margin',
  whereProcess:'Where to process',homeSystem:'Home system',crew:'Characters',
  build:'Build plan',noPlan:'No plan yet. Open the Settings tab.',
  colonies:'colonies',mining:'extraction',processing:'processing',
  details:'Details',back:'Back',reserveOk:'headroom everywhere',
  noReserve:'no headroom',sortLoad:'By load',sortReserve:'By remaining headroom',
  sortPilot:'By character name',sortSystem:'By system',
  cProc:'processing',cMine:'extraction',addCharacter:'Add character',adminPanel:'Admin panel',
  unlink:'Unlink',unlinkHint:'Erases the token on our side — the planner stops seeing this character until you log in again. Access on the CCP side is revoked separately at eveonline.com/account/third_party_apps.',
  reconnect:'Reconnect',reconnectHint:'ESI access for this character has expired or been revoked (password changed, app access revoked) — colonies and skills are no longer updating. Log in again through EVE SSO with this same character.',
  unlinkFail:'Could not unlink the character',
  groupHint:'Make this character the group\'s primary — logging in on a new device or browser with any member will pull in all the others under it.',
  groupSave:'Make primary',groupSaved:'Now primary',groupSaveFail:'Could not set as primary',
  groupPrimary:'primary',
  pocoProfitTitle:'Plan profitability forecast',
  pocoRevenue:'Revenue / mo',pocoTax:'POCO tax / mo',pocoPurchaseCost:'P1 purchase / mo',pocoNetProfit:'Net profit / mo',
  pocoMissingPrices:'No price: {list}',pocoMissingRates:'POCO rate unknown: {list}',
  pocoMissingVolumes:'No unit volume data (causeway throughput not accounted for): {list}',
  pocoLogisticsBottleneck:'The chain is limited by causeway throughput — real share of working time:',
  pocoHigherThanBest:'This POCO rate is higher than on other processing colonies in the plan',
  pocoIncomplete:'Net profit is not shown until every rate is known and every price is available — a partial sum would look more accurate than it really is.',
  pocoAssumption:'Estimate over {hours} hours per month, no deposit depletion. Downtime from causeway throughput is already accounted for (see above); other downtime is not. Tax applies on both export and import at every hop between planets in the chain.',
  pocoRevenueByProductTitle:'Revenue by product',pocoNotesTitle:'Assumptions & warnings',
  pocoRevenueByProductQty:'Units / mo',pocoRevenueByProductPrice:'Price, ISK',pocoRevenueByProductRevenue:'Revenue / mo',
  purchaseListTitle:'Raw material purchase list (P1)',
  purchaseListProduct:'Product',purchaseListQty:'Qty / mo',purchaseListPrice:'Price, ISK',
  purchaseListCost:'Cost / mo',purchaseListTotal:'Total',purchaseListNoPrice:'no price',
  purchaseListAsOf:'Prices as of plan build{stamp}',purchaseListNoSnapshot:' (no price snapshot found)',
  coloniesProfitTitle:'Actual profitability forecast',
  coloniesProfitMissingOutput:'Current rate unknown: {list}',
  coloniesProfitAssumption:'By each colony’s real current rate (adjusted for extraction decay and factory downtime), not the theoretical maximum. Tax only on what leaves the planet itself — not tracked further between different colonies, since ESI does not report that link.',
  chipJobs:'jobs',jobsOk:'ok',jobsStale:'stuck',jobsFailing:'failing',
  jobsNever:'never ran',jobsFailingCount:'failing {n} time(s) in a row',
  jobServerStatus:'Server status',jobMarketPrices:'Market prices',
  jobRefreshTokens:'ESI token refresh',jobSyncSkills:'Character skills',
  jobSyncColonies:'Colony status',jobBackup:'Backup',jobRefreshSde:'Planet skeleton from SDE',
  unlinkedRevoked:'Character unlinked — access revoked on CCP’s side too',
  unlinkedLocalOnly:'Character unlinked on our side (no app secret configured — real revocation unavailable)',
  planetSlots:'planet slots',purchaseP1:'Buy all P1 on the market (do not build extraction)',wholeRegion:'Whole region',clearAll:'Clear all',nothingChosen:'nothing selected',systems:'systems',selectAll:'Select all',pickProducts:'Select at least one product',pickConst:'Select at least one constellation',export:'Export to Excel',jumpPlan:'↑ Plan',jumpColonies:'↓ My colonies',jumpShopping:'↓ Shopping list',shopping:'Shopping list: command centres',shopTotal:'Command centres in total',exportEmpty:'Nothing to export: no plan and no colonies',exportFail:'Export failed',profitTitle:'What is most profitable to produce',collectedAgo:'snapshot taken',minutesAgo:'min ago',stale:'stale',colProduct:'Product',colColonies:'Planets',colPerColony:'ISK / colony-hour',colPerHour:'ISK / hour',colPrice:'Unit price',noPrice:'no price',noSnapshot:'No price snapshot yet',advDeficit:'Not enough characters for this chain',advSurplus:'More characters than needed',advDeficitLead:'A full cycle needs {need} colonies, the pool has {have} slots. These fit entirely:',advSurplusLead:'Your choice takes {need} of {have} colonies. {spare} free — you could add:',advTake:'Take',advIgnoreDeficit:'Build as is',advIgnoreSurplus:'Put spare on extraction',advIgnoreDeficitHint:'the plan will report a raw material shortage',advIgnoreSurplusHint:'extraction of the same chain, scarcest first',advDuplicateChain:'You can repeat the whole chain {n} more time(s)',linesPerTarget:'Lines per chain',colonies3:'colonies',excess:'Extraction (surplus)',mSpread:'Systems per character',logi:'systems used in total',systemsAvg:'systems each',sysOne:'system',sysFew:'systems',sysMany:'systems',logiGood:'colonies are compact',logiPoor:'harvesting will need travel',mColonies:'Colonies in the plan',mChars:'Characters used',mPeak:'Most loaded',mShort:'Characters missing',planClosed:'plan is complete',fitsUpTo:'a double template fits planets up to',km:'km',suitableHere:'suitable planets in this system',ofThem:'of them',decisionTitle:'The plan hit planet size limits',decisionText:'No planet in the chosen home system fits a double template. Pick how to proceed.',chooseOther:'Pick another system',useSingle:'Use single templates',singleNote:'twice as many planets and characters will be needed',savedPlans:'Saved plans',save:'Save',load:'Open',del:'Delete',compare:'Compare',noSaved:'Nothing saved yet',saveFirst:'Build a plan first',pickTwo:'Tick two plans to compare',cmpTitle:'Difference',colonies2:'colonies',chars2:'characters',peak2:'peak load',onlyLeft:'Only in the first',onlyRight:'Only in the second',same:'Unchanged',
  noPlanLong:'No plan yet. Open the Settings tab, pick products and build a plan.',
  planEmpty:'The plan is empty.',searchPh:'search…',planNamePh:'plan name',
  crewPre:'They appear after the calculation: the planner spreads characters across planets by their skills.',
  dataFailed:'load failed',snapshotMade:'Snapshot taken',
  profitNote:'Ranked by ISK per colony-hour: the scarce resources are planets and characters. POCO tax, hauling and market fees are not counted, so the figures are an upper bound.',
  noSnapshotHint:'Collect it: python -m scripts.refresh_market_prices — or the scheduler python -m scripts.scheduler, which does it on a schedule.',
  pgFrom:'Power breakdown',sStruct:'structures',sHeads:'heads',sLinks:'links',
  capCap:'Capacity',capContent:'Contents',cycNow:'Current cycle',cycNone:'no cycle data',
  extractsW:'Extracts',heads10:'10 heads',cycUnknown:'Cycle state unknown',
  extrRateNow:'Now',extrRateAvg:'Average',extrRateUnit:'units/h',
  extrHistoryBuilding:'building up history',
  extrDeficit:'deficit — needs extra extraction',
  extrDeficitChain:'deficit — {product} chain at risk, add another mining colony',
  afterGame:'appears once connected to the game',producesW:'Produces',inputW:'Input',
  pHead:['head','heads','heads'],emptyStorage:'empty',
  inProduction:'In production',cycIdleSince:'Idle',
  cycProjectedHint:'confirmed as of the last time the colony was opened in-game, unknown since',
  cycNeverStarted:'Never started yet',
  sharedExtract:'This planet has {n} of our extractors on the same resource — each will yield below the estimate.',
  onPlan:'on the plan',onPlanets:'across',
  thColony:'Colony',thColonySub:'who and where',thProd:'Production',thProdSub:'what it makes and from what',
  thLoad:'Load',thLoadSub:'how it adds up and what is left',thChar:'Character',thPlanet:'Planet',
  thProduct:'Product',thPgFrom:'PG breakdown',thReserve:'Headroom',
  pColony:['colony','colonies','colonies'],pPlanet:['planet','planets','planets'],
  pNote:['note','notes','notes'],pConst:['constellation','constellations','constellations'],
  pProd:['product','products','products'],
  ssoUnavailable:'Login unavailable',
  notJson:'{api} returned non-JSON. Run “python run.py” and open {flask}/',
  pPlanetN:['planet','planets','planets'],coloniesIdle:'idle',coloniesAttention:'need attention',
  coloniesDeficit:'in deficit',
  launchpadFull:'launchpad full',needsAttention:'Needs attention',freeSlot:'free planet slot',
  themeTitle:'Theme',moreTitle:'More',viewList:'By character',viewGrid:'Grid',sortTitle:'Sort',loadLbl:'Load',
  inGameColonies:'My in-game colonies',cycOver:'program ended',cycIdle:'no program running',
  hUnit:'h',minUnit:'min',dUnit:'d',pPin:['pin','pins','pins'],
  progEnds:'until the extraction program ends',syncedColony:'built in game',
  procCycleLbl:'until the processing cycle ends',
  procDepletionLbl:'until raw materials in the launchpad run out',
  cycNoFactory:'ESI does not expose factory state — extractors only',
  role_mine:'Extraction',role_mine_surplus:'Extraction (surplus)',role_proc:'Processing',role_direct_p2:'Direct P2',
  factW:'factories',pFactory:['factory','factories','factories']}
};
const t=k=>(T[lang]||T.ru)[k]||k;

/* Доступ к localStorage может быть запрещён: приватный режим, отключённые
   cookie, открытие страницы как файла. Необёрнутое обращение выбрасывает
   исключение и роняет весь скрипт — приложение выглядит нерабочим.
   Настройки при этом не сохранятся, но всё остальное продолжит работать. */
const store={
  get(k,fallback){ try{ return localStorage.getItem(k) ?? fallback; }catch(e){ return fallback; } },
  set(k,v){ try{ localStorage.setItem(k,v); }catch(e){ /* сохранять некуда — не беда */ } }
};

function applyLang(){
  document.documentElement.lang=lang;
  document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=t(el.dataset.i18n));
  document.querySelectorAll('[data-i18n-ph]').forEach(el=>el.placeholder=t(el.dataset.i18nPh));
  document.querySelectorAll('[data-i18n-title]').forEach(el=>el.title=t(el.dataset.i18nTitle));
  document.querySelectorAll('.lang button').forEach(b=>b.classList.toggle('on',b.dataset.lang===lang));
  store.set('pi-lang',lang);
  renderDataChip(); renderMeta();
  buildSortMenu(); renderCrew(); renderDash(); renderShopping(); renderProfit(); renderPocoProfit(); renderColonies(); renderColoniesProfit(); renderSaved();
  if(allProducts.length){ renderProducts(); renderConstellations(); } if(plan.length){renderAnswers(); renderTable();}
  if(document.getElementById('modal').classList.contains('on')) openModal(currentModal);
  if(currentColony && document.getElementById('colonyPage').classList.contains('on')) openColony(currentColony);
  if(typeof relocalizePlan==='function') relocalizePlan();  // предупреждения сервера — на новый язык
  if(typeof loadAdvice==='function' && adviceData) loadAdvice();  // панель вместимости — тоже с сервера
}

/* ── Тема ──────────────────────────────────────────────────────── */
function toggleTheme(){ setTheme(document.body.classList.contains('light')?'dark':'light'); }
function setTheme(name){
  const root=document.documentElement;
  root.classList.add('theme-switching');
  document.body.classList.toggle('light',name==='light');
  // Форсируем пересчёт, пока переходы выключены: иначе браузер
  // объединит добавление и снятие класса в один кадр.
  void document.body.offsetWidth;
  requestAnimationFrame(()=>requestAnimationFrame(()=>root.classList.remove('theme-switching')));
  store.set('pi-theme',name);
}

const STRUCT={
  ru:{command_center:'Командный центр',launchpad:'Причал',storage_facility:'Склад',
    extractor_control_unit:'Экстрактор',basic_industry_facility:'Базовая фабрика',
    advanced_industry_facility:'Продвинутая фабрика',high_tech_industry_facility:'Высокотехнологичная фабрика'},
  en:{command_center:'Command centre',launchpad:'Launchpad',storage_facility:'Storage',
    extractor_control_unit:'Extractor',basic_industry_facility:'Basic factory',
    advanced_industry_facility:'Advanced factory',high_tech_industry_facility:'High-tech factory'}};
const sname=kind=>(STRUCT[lang]||STRUCT.ru)[kind]||kind;
/* Роль колонии: сервер шлёт role_key (mine|mine_surplus|proc|direct_p2)
   и role_tier («P2/P3»); строка role остаётся русской и не для показа. */
const ROLE_CLASS={mine:'mine',mine_surplus:'excess',proc:'proc',direct_p2:'direct'};
const roleClass=row=>ROLE_CLASS[row.role_key]||'proc';
const roleLabel=row=>t('role_'+(row.role_key||'proc'))+(row.role_tier?' '+row.role_tier:'');
const isMining=row=>row.role_key==='mine'||row.role_key==='mine_surplus';
/* Число фабрик: сервер шлёт factory_summary как данные, строка structures
   осталась русской (нужна экспорту). */
function factoryLabel(row){
  const fs=row.factory_summary||{};
  if(fs.advanced!=null) return `${fs.advanced} ${t('factW')} + ${fs.basic} basic`;
  if(fs.factories!=null) return pl(fs.factories,'pFactory');
  return row.structures||'';
}
/* Номер планеты -> римская цифра, как в самой игре («WMH-SO IV», не
   «16.0»/«16») — 18.09.2026, по прямому запросу пользователя: план
   показывал дробный номер (CSV-колонка «Planet» приходит от сервера
   как «16.0» — pandas читает её float64), а игра номер планеты вообще
   никогда не показывает арабской цифрой. Только для ОТОБРАЖЕНИЯ:
   исходное поле .planet остаётся числом везде, где по нему сравнивают/
   сортируют/шлют на сервер (matchColony(), matchPlanRow(), сортировка
   по системе, колонки profitability) — иначе parseInt("XVI",10) сломал
   бы сопоставление плана с настоящими колониями. */
const ROMAN_NUMERALS=[[1000,'M'],[900,'CM'],[500,'D'],[400,'CD'],[100,'C'],[90,'XC'],
  [50,'L'],[40,'XL'],[10,'X'],[9,'IX'],[5,'V'],[4,'IV'],[1,'I']];
function romanNumeral(value){
  let n=Math.round(parseFloat(value));
  if(!Number.isFinite(n)||n<=0) return value==null?'':String(value);
  let out='';
  for(const [v,sym] of ROMAN_NUMERALS){ while(n>=v){ out+=sym; n-=v; } }
  return out;
}
/* Число в языковом формате и множественное число под язык. */
const nloc=n=>Number(n).toLocaleString(lang==='en'?'en-US':'ru-RU');
const pl=(n,forms)=>{ const f=(T[lang]||T.ru)[forms]||['','',''];
  return lang==='ru'?plural(n,f[0],f[1],f[2]):`${n} ${n===1?f[0]:f[2]}`; };
const RING={command_center:'--ring-infra',launchpad:'--ring-infra',storage_facility:'--ring-infra',
  extractor_control_unit:'--ring-extract',basic_industry_facility:'--ring-basic',
  advanced_industry_facility:'--ring-adv',high_tech_industry_facility:'--ring-adv'};

const SORTS=[{key:'load',t:'sortLoad'},{key:'reserve',t:'sortReserve'},
             {key:'pilot',t:'sortPilot'},{key:'system',t:'sortSystem'}];
function buildSortMenu(){
  const pop=document.getElementById('sortPop'); if(!pop) return;
  pop.innerHTML=SORTS.map(x=>`<button onclick="setSort('${x.key}')">
    <span class="mark">${x.key===sortBy?'✓':''}</span>${t(x.t)}</button>`).join('');
}

// Имена планов вводит пользователь, имена персонажей приходят из ESI —
// в innerHTML только через esc() (найдено внешней рецензией 28.09.2026).
function esc(v){
  return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function initials(name){
  return String(name||'').split(/\s+/).filter(Boolean).slice(0,2)
    .map(w=>w[0].toUpperCase()).join('');
}

function plural(n,one,few,many){
  if(lang!=='ru') return `${n} ${n===1?one:many}`;
  const a=Math.abs(n)%100,b=a%10;
  if(a>10&&a<20)return `${n} ${many}`; if(b>1&&b<5)return `${n} ${few}`;
  if(b===1)return `${n} ${one}`; return `${n} ${many}`;}

/* Иконка «чем занята колония» для сетки/списка «Моих колоний в игре» —
   по реальному составу пинов (row.structures_detail), не по роли:
   есть экстрактор — его иконка, иначе самая старшая по тиру фабрика,
   иначе склад/причал. Ничего не выдумывается — то, что реально стоит. */
function dominantStructureIconId(row){
  const kinds=(row.structures_detail||[]).map(s=>s.kind);
  const priority=['extractor_control_unit','high_tech_industry_facility',
    'advanced_industry_facility','basic_industry_facility','launchpad','storage_facility'];
  const kind=priority.find(k=>kinds.includes(k));
  return kind?ids['structure:'+kind]:null;
}

/* То, ЧТО колония производит (иконка + имя, а не иконка постройки) — по
   реальным данным пинов (row.pins, см. scripts/sync_colony_status.py::
   pin_detail): у добычи — выход её СОБСТВЕННОЙ фабрики (basic_industry_
   facility) — P1-продукт, тот же, что показан на кружочке этого пина
   в детальном виде колонии (structOrbsForRow()), а не сырьё P0, которое
   тянет экстрактор с земли и которое на этой же планете сразу
   перерабатывается в P1 (миф "добывающая колония продаёт сырьё" —
   найдено пользователем 16.09.2026, до этого кружок показывал P0).
   У переработки — продукт самой старшей по тиру фабрики (high-tech,
   иначе advanced, иначе basic) — она и даёт конечный продукт цепочки
   на этой колонии. Если пины ещё не пересинхронизированы новым кодом
   (row.pins пуст) — честный откат на иконку постройки без имени, как
   раньше; для добычи — дополнительный откат на сырьё экстрактора,
   если у колонии почему-то нет собственной фабрики (не должно
   случаться для настоящего шаблона miner_00, но пины могут быть ещё
   не полностью пересинхронизированы). */
/* Пин, чей продукт колония фактически отдаёт дальше (не обязательно тот
   же, что добывает экстрактор — см. producedInfo). Вынесено отдельно от
   producedInfo(), чтобы colonyOutputRatePerHour() (прибыльность настоящих
   колоний, docs/ROADMAP.md, Фаза 9, 16.09.2026) могла взять именно этот
   пин для расчёта текущей скорости — не пересобирать логику дважды. */
function colonyOutputFlow(row){
  const pins=row.pins||[];
  if(row.role_key==='mine'){
    const factory=pins.find(p=>p.kind==='basic_industry_facility'&&p.product);
    if(factory) return {pin:factory, extracted:false};
    const extractors=pins.filter(p=>p.kind==='extractor_control_unit'&&p.product);
    const match=extractors.find(p=>p.expiry_time===row.nearest_expiry)||extractors[0];
    if(match) return {pin:match, extracted:true};
  } else if(row.role_key==='proc'){
    const priority=['high_tech_industry_facility','advanced_industry_facility','basic_industry_facility'];
    for(const kind of priority){
      const pin=pins.find(p=>p.kind===kind&&p.product);
      if(pin) return {pin, extracted:false};
    }
  }
  return null;
}

function producedInfo(row){
  const flow=colonyOutputFlow(row);
  if(flow) return {iconId:ids[flow.pin.product], name:flow.pin.product, extracted:flow.extracted};
  return {iconId:dominantStructureIconId(row), name:null};
}

/* Пин причала/склада с известным объёмом (используется и для процента
   заполнения, и для текста в подсказке круга — см. orb()). sim
   (необязательно, results simulateColonyFactories()) — если пин там
   есть с известной projected usedM3, объём считается известным и по
   ней, даже когда сам снимок пина used_m3 ещё null или устарел. */
function launchpadPin(row, sim){
  const pins=row.pins||[];
  const known=p=>p.used_m3!=null||(sim&&sim.get(p.pin_id)&&sim.get(p.pin_id).usedM3!=null);
  return pins.find(p=>p.kind==='launchpad'&&known(p))
    || pins.find(p=>p.kind==='storage_facility'&&known(p)) || null;
}

/* Заполненность причала (в крайнем случае — склада) в процентах от
   ёмкости — по умолчанию реальный объём содержимого (used_m3) против
   игровой константы (capacity_m3), оба с сервера
   (scripts/sync_colony_status.py). Если передан sim (проекция
   simulateColonyFactories() на «сейчас») и в нём есть usedM3 для этого
   пина — используется он: занятый объём меняется со временем (сырьё
   убывает, продукция копится, а у них обычно разный объём на единицу),
   и статичный снимок с последней синхронизации не поспевает за этим
   (найдено пользователем 17.09.2026 — кольцо заполненности на круге
   колонии стояло на месте, хотя реальный объём в игре заметно менялся).
   null, если пины ещё не пересинхронизированы или объём хоть одного
   предмета не удалось узнать — тогда кольцо не рисуется вовсе, а не
   рисуется пустым/полным наугад (правило 1). */
function launchpadFillPct(row, sim){
  const pin=launchpadPin(row, sim);
  if(!pin) return null;
  const pinSim=sim&&sim.get(pin.pin_id);
  const usedM3=(pinSim&&pinSim.usedM3!=null)?pinSim.usedM3:pin.used_m3;
  return usedM3!=null?Math.min(100,100*usedM3/pin.capacity_m3):null;
}

// 99%, не 100: объём считается из объёма-на-единицу с округлением (ESI
// отдаёт его как есть, мы ничего не подгоняем), и почти забитый причал
// практически всегда означает «добывать больше некуда» так же, как
// ровно полный — ждать точного 100% ради индикатора не нужно.
const LAUNCHPAD_FULL_PCT=99;
function isLaunchpadFull(row, sim){
  const pct=launchpadFillPct(row, sim);
  return pct!=null&&pct>=LAUNCHPAD_FULL_PCT;
}

// Порог дефицита добычи — фиксированная величина по прямому решению
// пользователя (11.09.2026): колония, добывающая меньше, уже дефицитна
// для всей цепочки сама по себе, независимо от истории конкретной
// планеты (плотность месторождения у каждой своя, но 48 000/ч — тот
// минимум, ниже которого ресурс всё равно приходится дублировать
// добычей). Осознанное отступление от общего принципа «порог — из
// истории колонии, не норматив» (см. docs/ROADMAP.md, Фаза 7).
const DEFICIT_UNITS_PER_HOUR=48000;

// ESI отдаёт qty_per_cycle как БАЗОВОЕ значение программы экстрактора
// (задаётся один раз при установке, не меняется само по себе) — не
// текущий фактический объём цикла. Реальная добыча затухает от этого
// значения по игровой механике (кривая с шумовой модуляцией) на
// протяжении всей программы, что подтверждено скриншотом игрового
// клиента пользователя (13.09.2026): игра — 17 030 ед./ч, наш прежний
// расчёт «в лоб» (qty_per_cycle×циклов/час) — 21 868–22 061, разница
// ~28% на давно идущей программе. Формула затухания не задокументирована
// CCP официально, но воспроизведена независимо несколькими сторонними
// PI-инструментами по их открытым исходникам и чейнджлогам — правило 2
// (сверка нескольких источников) соблюдено. Ошибка была тем более
// значима, что искажала DEFICIT_UNITS_PER_HOUR: заниженный дефицит
// выглядел как «всё в порядке».
function extractorDecayedQty(baseValue, installTimeIso, cycleSeconds, atMs){
  if(baseValue==null||!installTimeIso||!cycleSeconds) return null;
  const startS=Date.parse(installTimeIso)/1000;
  const timeDiff=atMs/1000-startS;
  if(!(timeDiff>=0)) return null;
  const cycleNum=Math.max(Math.floor((timeDiff+1)/cycleSeconds)-1,0);
  const barWidth=cycleSeconds/900;
  const t=(cycleNum+0.5)*barWidth;
  const decayValue=baseValue/(1+t*0.012);
  const phaseShift=Math.pow(baseValue,0.7);
  const sinStuff=Math.max(0,(Math.cos(phaseShift+t/12)+Math.cos(phaseShift/2+t/5)+Math.cos(t/2))/3);
  const barHeight=decayValue*(1+0.8*sinStuff);
  return Math.floor(barWidth*barHeight);
}

// Расчётная скорость добычи ОДНОГО экстрактора прямо сейчас — затухшее
// значение (см. extractorDecayedQty) на текущий момент, пересчитанное
// в «единиц в час».
function extractorRatePerHour(pin){
  const qty=extractorDecayedQty(pin.qty_per_cycle, pin.install_time, pin.cycle_seconds, Date.now());
  if(qty==null) return null;
  return qty*3600/pin.cycle_seconds;
}

/* Текущая скорость выхода КОЛОНИИ — то же самое, что физически покидает
   планету (см. colonyOutputFlow), для прибыльности настоящих колоний
   (docs/ROADMAP.md, Фаза 9, 16.09.2026). Не теоретический максимум, а
   факт прямо сейчас — прямое решение пользователя:
   - добыча без своей фабрики: затухшая скорость экстрактора
     (extractorRatePerHour) — уже реальная величина, ничего досчитывать
     не нужно;
   - добыча со своей фабрикой и переработка: номинальная скорость схемы
     (data/schematics.json, правило 2), но ТОЛЬКО если pinCycleInfo (с
     учётом simulateColonyFactories — тех же данных, что красят кольцо
     на кружке пина) подтверждает, что цикл идёт СЕЙЧАС — простаивающая
     фабрика честно даёт 0, а не номинал. Если состояние неизвестно
     (снимок устарел, симуляции нет) — честный null, не 0 и не номинал:
     это ровно то же самое различие, что pinCycleInfo уже проводит между
     idle/unknown для самого кружка пина.
   simResult вычисляется один раз на колонию и передаётся вызывающей
   стороной (как и в structOrbsForRow) — не пересчитывать симуляцию
   на каждый вызов. */
function colonyOutputRatePerHour(row, simResult){
  const flow=colonyOutputFlow(row);
  if(!flow) return null;
  if(flow.extracted) return extractorRatePerHour(flow.pin);
  const info=pinCycleInfo(flow.pin, row.game_last_update, simResult&&simResult.get(flow.pin.pin_id));
  if(info.unknown || info.unknownSince) return null;
  if(info.idle) return 0;
  const typeId=ids[flow.pin.product];
  const schem=typeId!=null && schematics[String(typeId)];
  if(!schem || !flow.pin.cycle_minutes) return null;
  return schem.output_qty*60/flow.pin.cycle_minutes;
}

// Средняя скорость по накопленной истории (extraction_history — копится
// sync_colony_status.py при каждой синхронизации, начиная с 11.09.2026;
// ESI сама историю не хранит). Каждый снимок пересчитан затуханием НА
// МОМЕНТ этого снимка (install_time считается неизменным — программа не
// перезапускалась; если экстрактор переустановили, старые снимки этого
// не знают, honest edge case). null, если снимков ещё нет — «копим
// историю», а не выдуманное число за несуществующий период.
function extractorAvgRatePerHour(pin){
  const hist=pin.extraction_history||[];
  if(!pin.install_time) return null;
  const rates=hist.map(s=>{
    const qty=extractorDecayedQty(s.qty_per_cycle, pin.install_time, s.cycle_seconds, Date.parse(s.sampled_at));
    return qty!=null&&s.cycle_seconds?qty*3600/s.cycle_seconds:null;
  }).filter(r=>r!=null);
  if(!rates.length) return null;
  return rates.reduce((a,b)=>a+b,0)/rates.length;
}

// Точки для графика (extractorRateChartSVG) — та же затухшая скорость,
// вычисленная на момент каждого снимка истории (см. extractorAvgRatePerHour).
function extractorRateHistoryPoints(pin){
  const hist=pin.extraction_history||[];
  if(!pin.install_time) return [];
  return hist.map(s=>{
    const qty=extractorDecayedQty(s.qty_per_cycle, pin.install_time, s.cycle_seconds, Date.parse(s.sampled_at));
    return qty!=null&&s.cycle_seconds?qty*3600/s.cycle_seconds:null;
  }).filter(r=>r!=null);
}

/* Простой столбчатый график скорости добычи по накопленной истории —
   без библиотек, только то, что реально накоплено (rates уже в
   единицах/час, см. extractorAvgRatePerHour). */
function extractorRateChartSVG(rates){
  const w=240,h=36,max=Math.max(...rates,1);
  const bw=w/rates.length;
  const bars=rates.map((r,i)=>{
    const bh=Math.max(1,h*r/max);
    return `<rect x="${i*bw}" y="${h-bh}" width="${Math.max(1,bw-1)}" height="${bh}" fill="var(--ring-extract)"/>`;
  }).join('');
  return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;max-width:240px;height:36px;margin-top:6px;display:block">${bars}</svg>`;
}

/* Блок «Сейчас/В среднем» под экстрактором — текущая расчётная скорость
   (extractorRatePerHour, всегда доступна, если запущена программа),
   средняя по накопленной истории (extractorAvgRatePerHour, null, пока
   снимков нет) и предупреждение о дефиците (см. DEFICIT_UNITS_PER_HOUR). */
/* Дефицитный ресурс угрожает цепочке — только если эту цепочку прямо
   сейчас держит НАСТОЯЩАЯ колония того же пользователя (coloniesData),
   а не расчётный план (Фаза 7, решение пользователя 11.09.2026: «это
   касается только если персонажи уже что-то производят — либо цепочка
   была при входе, либо построена по плану и стала настоящей колонией»).
   recipeInputs — что каждый продукт потребляет на входе (/api/initial-data,
   data/recipes.json, правило 2), пары строятся по РЕАЛЬНЫМ пинам фабрик
   всех колоний, план сюда не подмешивается. */
function realChainProductsUsing(resource){
  const products=new Set();
  coloniesData.forEach(c=>(c.pins||[]).forEach(pin=>{
    if(!pin.kind||!pin.kind.endsWith('industry_facility')||!pin.product) return;
    if((recipeInputs[pin.product]||[]).includes(resource)) products.add(pin.product);
  }));
  return [...products];
}

/* Один экстрактор — дефицит прямо сейчас (extractorRatePerHour ниже
   DEFICIT_UNITS_PER_HOUR) и, если есть, реальные продукты, чья цепочка от
   него зависит (см. realChainProductsUsing). atRisk пуст, если дефицитный
   ресурс сейчас никем из настоящих колоний не потребляется — предупреждение
   всё равно показывается (правило 1: колония дефицитна сама по себе), но
   без утверждения об угрозе конкретной цепочке, которого нет. */
function pinDeficitInfo(pin){
  const rate=extractorRatePerHour(pin);
  if(rate==null || rate>=DEFICIT_UNITS_PER_HOUR) return {deficient:false, atRisk:[]};
  return {deficient:true, atRisk: pin.product?realChainProductsUsing(pin.product):[]};
}

/* Колония в дефиците добычи — хотя бы один её экстрактор прямо сейчас
   даёт меньше DEFICIT_UNITS_PER_HOUR. Тот же сигнал «требует внимания»,
   что и истекающий таймер/забитый причал — ресурс всё равно приходится
   дублировать добычей (решение пользователя 11.09.2026). */
function isColonyDeficient(row){
  if(row.role_key!=='mine') return false;
  return (row.pins||[]).some(p=>p.kind==='extractor_control_unit' && pinDeficitInfo(p).deficient);
}

/* Реальные продукты, чьи цепочки под угрозой из-за дефицита ЭТОЙ колонии —
   объединение atRisk всех её дефицитных экстракторов (обычно один). */
function colonyDeficitRisk(row){
  if(row.role_key!=='mine') return [];
  const products=new Set();
  (row.pins||[]).forEach(p=>{
    if(p.kind!=='extractor_control_unit') return;
    pinDeficitInfo(p).atRisk.forEach(x=>products.add(x));
  });
  return [...products];
}

/* Текст предупреждения о дефиците — с указанием цепочки под угрозой,
   когда она известна (см. colonyDeficitRisk/pinDeficitInfo.atRisk), иначе
   общий текст. plain=true — без HTML (для атрибута title). */
function deficitText(atRisk,plain){
  if(!atRisk.length) return t('extrDeficit');
  const tpl=t('extrDeficitChain');
  return plain ? tpl.replace('{product}',atRisk.join(', ')) : fmt(tpl,{product:atRisk.join(', ')});
}

function extractorRateHTML(pin){
  const now=extractorRatePerHour(pin);
  if(now==null) return '';
  const avg=extractorAvgRatePerHour(pin);
  const info=pinDeficitInfo(pin);
  const rates=extractorRateHistoryPoints(pin);
  return `<div class="meta" style="margin-top:8px">
    <div style="display:flex;gap:16px;flex-wrap:wrap">
      <span>${t('extrRateNow')}: <b style="color:${info.deficient?'var(--alarm)':'var(--read)'}">${nloc(Math.round(now))} ${t('extrRateUnit')}</b></span>
      <span>${t('extrRateAvg')}: <b>${avg!=null?`${nloc(Math.round(avg))} ${t('extrRateUnit')}`:t('extrHistoryBuilding')}</b></span>
    </div>
    ${info.deficient?`<div style="color:var(--alarm);margin-top:4px">⚠ ${deficitText(info.atRisk,false)}</div>`:''}
    ${rates.length>1?extractorRateChartSVG(rates):''}
  </div>`;
}

/* ── Круг колонии ───────────────────────────────────────────────
   Кольцо показывает загрузку по узкому ресурсу, а не таймер цикла:
   таймеров у нас пока нет, и рисовать вымышленное число нельзя.

   row.isReal — та же функция для факта из игры («Мои колонии в игре»,
   см. realRows()) — вид как в самой игре (обзорная сетка): текстура
   планеты крупным планом, плашка с обратным отсчётом сверху-слева,
   иконка добываемого/производимого ресурса по центру, портрет персонажа
   справа-снизу. Кольцо здесь — не CPU/PG (тех честно нет, ESI не отдаёт
   радиус планеты, правило 1), а заполненность причала — те же данные,
   что в самой игре. */
/* showPilot=false — там, где портрет персонажа уже показан рядом
   (карточка колонии, ряд «Список по персонажам») и повторять его на
   самом круге было бы дублем; в сетке, где на один экран приходится
   много разных персонажей и рядом с кругом портрета больше нигде нет,
   остаётся включённым по умолчанию. */
function orb(row,size,showPilot=true){
  const r=(size/2)-2, c=2*Math.PI*r;
  const planetId=ids['planet:'+row.planet_type];
  if(row.isReal){
    // У добычи exp — настоящая метка ESI (nearest_expiry). У переработки
    // такой метки в игре нет вовсе — вместо неё то же sim.depletionMs, что
    // и в colonyTail() (панель «Детали»): время до первого исчерпавшегося
    // вида сырья в причале, честно посчитанное simulateColonyFactories(),
    // переведённое в такую же абсолютную метку (Date.now()+depletionMs),
    // чтобы плашка тикала сама через общий setInterval по data-exp, как и
    // у добычи, а не пересчитывала симуляцию на каждый тик. Раньше бейдж
    // для переработки не рисовался вовсе ни в сетке, ни в списке по
    // персонажам — только в панели «Детали» (обнаружено пользователем
    // 16.09.2026 по скриншотам обоих видов).
    const isProc=row.role_key==='proc';
    // Считается один раз на орб и переиспользуется и для бейджа истощения
    // (только переработка), и для кольца заполненности причала ниже
    // (любая роль) — не пересчитывать одну и ту же симуляцию дважды.
    const sim=simulateColonyFactories(row.pins,row.routes,row.game_last_update);
    const exp=isProc
      ? (()=>{ const ms=sim&&sim.depletionMs; return ms!=null?Date.now()+ms:null; })()
      : (row.nearest_expiry?Date.parse(row.nearest_expiry):null);
    const left=exp!=null?exp-Date.now():null;
    const over=left!=null&&left<=0;
    // Таймер показывается только там, где он есть по-настоящему (активный
    // экстрактор/посчитанное истощение причала) или где его отсутствие
    // само по себе сигнал (простаивающий экстрактор). Для переработки без
    // маршрутов/схем (считать нечем) — чип не рисуется совсем, как и
    // раньше, а не подставляется «нет данных» лишним значком на итак
    // плотной сетке.
    const showBadge=exp!=null||!isProc;
    const badgeTxt=exp!=null?fmtBadgeLeft(left):t('cycIdle');
    const info=producedInfo(row);
    const pin=launchpadPin(row, sim);
    const fillPct=launchpadFillPct(row, sim);
    // Та же проекция, что и у fillPct — иначе полоса в подсказке
    // показывала бы честный (пересчитанный) процент, а число рядом с ней
    // оставалось от статичного снимка синхронизации, что противоречило
    // бы само себе.
    const pinSimForTip=pin&&sim&&sim.get(pin.pin_id);
    const pinUsedM3=(pinSimForTip&&pinSimForTip.usedM3!=null)?pinSimForTip.usedM3:(pin&&pin.used_m3);
    // Причал забит под завязку — сигнал ТОЛЬКО для добычи: складывать
    // больше некуда, это требует внимания не меньше, чем истекающая
    // программа (см. attnN в renderColonies()). У переработки полный
    // причал — запас сырья на будущее, скорее хорошо, не тревога
    // (переопределено 16.09.2026, docs/ROADMAP.md, Фаза 9) — кольцо для
    // неё остаётся обычным синим «заполненность», не переключается на
    // мигающее красное; сама проверка isLaunchpadFull() не меняется, она
    // используется и там, где роль не при чём (подсказка при наведении).
    const launchpadAlarm=!isProc&&isLaunchpadFull(row, sim);
    // «Требует внимания» — то же условие, что и в attnN сводки: программа
    // экстрактора кончилась (добыча) или причал забит (только добыча).
    // Центральная иконка ресурса в этом случае уступает место красному
    // «запрету» — как в игре; это своя иконка (UI-состояние, не тип
    // предмета из игры), поэтому рисуется SVG, а не берётся с
    // images.evetech.net. Дефицит добычи — отдельная категория (см.
    // deficitN в renderColonies()), не смешана сюда: иначе одна причина
    // маскировала бы другую в общем счёте, а у дефицита и так свой явный
    // маркер (.def-mark) и подсказка.
    const deficient=isColonyDeficient(row);
    const deficitRisk=deficient?colonyDeficitRisk(row):[];
    const attention=over||launchpadAlarm;
    const noEntry=`<svg class="cargo alert" viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="9.5"/><line x1="5.3" y1="18.7" x2="18.7" y2="5.3"/>
      </svg>`;
    const tip=`<div class="tip">
      ${attention?`<div class="tip-head">${t('needsAttention')}</div>`:''}
      ${deficient?`<div class="tip-meta" style="color:var(--amber)">⚠ ${deficitText(deficitRisk,false)}</div>`:''}
      ${info.name?`<div class="tip-row">${info.iconId?`<img src="${ICON(info.iconId)}" alt="">`:''}
        <span><b>${info.extracted?t('extractsW'):t('producesW')}</b><br>${info.name}</span></div>`:''}
      ${pin?`<div class="tip-label">${sname(pin.kind)}</div>
        <div class="tip-bar"><i style="width:${fillPct.toFixed(0)}%"></i></div>
        <div class="tip-meta">${nloc(Math.round(pinUsedM3))}/${nloc(pin.capacity_m3)} m³</div>`:''}
      <div class="tip-planet">${row.system} ${romanNumeral(row.planet)}</div>
      <div class="tip-meta">${row.planet_type} ${(T[lang]||T.ru).pPlanetN[0]}</div>
    </div>`;
    return `<div class="orb real${attention?' attn':''}" style="--orb:${size}px"
              onclick="openColony('${row.matchId}')"
              aria-label="${row.character} · ${row.system} ${romanNumeral(row.planet)}">
      <div class="disc">${planetId?`<img src="${ICON(planetId,128)}" alt="">`:''}</div>
      ${fillPct!=null?`<svg class="ring${launchpadAlarm?' full':''}" viewBox="0 0 ${size} ${size}">
        <circle class="track" cx="${size/2}" cy="${size/2}" r="${r}"/>
        <circle cx="${size/2}" cy="${size/2}" r="${r}" stroke="var(${launchpadAlarm?'--alarm':'--ring-infra'})"
          stroke-dasharray="${c*fillPct/100} ${c}" stroke-linecap="round"/>
      </svg>`:''}
      ${attention?noEntry:(info.iconId?`<img class="cargo" src="${ICON(info.iconId)}" alt="">`:'')}
      ${showBadge?`<div class="badge${over?' hot':''}"${exp!=null?` data-exp="${exp}"`:''}>${badgeTxt}</div>`:''}
      ${showPilot?`<div class="pilot" title="${row.character}">${initials(row.character)}${avatarImg(charIdOf(row.character))}</div>`:''}
      ${deficient?`<div class="def-mark" title="${deficitText(deficitRisk,true)}"></div>`:''}
      ${tip}
    </div>`;
  }
  const peak=Math.max(row.cpu_percent,row.pg_percent);
  const color=peak>=95?'var(--alarm)':peak>=90?'var(--amber)':
              (isMining(row)?'var(--ring-extract)':'var(--ring-adv)');
  const cargoId=row.type_id;
  return `<div class="orb" style="--orb:${size}px" onclick="openColony('${row.id}')"
            title="${row.character} · ${row.system} ${romanNumeral(row.planet)} · ${row.res_out}">
    <div class="disc">${planetId?`<img src="${ICON(planetId)}" alt="">`:''}</div>
    ${cargoId?`<img class="cargo" src="${ICON(cargoId)}" alt="">`:''}
    <div class="arrows"><svg viewBox="0 0 40 40">
      <path d="M20 4 v9 M16 8 l4-4 4 4"/><path d="M20 36 v-9 M16 32 l4 4 4-4"/>
      <path d="M4 20 h9 M8 16 l-4 4 4 4"/><path d="M36 20 h-9 M32 16 l4 4-4 4"/>
    </svg></div>
    <svg class="ring" viewBox="0 0 ${size} ${size}">
      <circle class="track" cx="${size/2}" cy="${size/2}" r="${r}"/>
      <circle cx="${size/2}" cy="${size/2}" r="${r}" stroke="${color}"
        stroke-dasharray="${c*Math.min(peak,100)/100} ${c}" stroke-linecap="round"/>
    </svg>
    <div class="badge${peak>=95?' hot':''}">${peak.toFixed(0)}%</div>
    ${showPilot?`<div class="pilot" title="${row.character}">${initials(row.character)}${avatarImg(charIdOf(row.character))}</div>`:''}
  </div>`;
}

/* Подсказка .orb .tip центрирована под кругом (см. CSS) — у крайних левых/
   правых кругов сетки центр выводит её за край экрана, часть текста не
   видна (16.09.2026, найдено пользователем на «Мои колонии в игре», вид
   «Сетка»). Число колонок сетки зависит от ширины окна (flex-wrap), поэтому
   заранее CSS-правилом не определить, какой круг крайний — сдвиг считается
   в JS через getBoundingClientRect() и кладётся в CSS-переменную --tip-shift
   (её читает transform и базового, и :hover/:focus-within состояния —
   сдвиг по X одинаков в обоих, меняется только Y и прозрачность).
   Считается СРАЗУ ПОСЛЕ вставки кругов в DOM (fixTipOverflowIn), а не по
   событию наведения: `transform` в CSS участвует в transition вместе с
   translateY-подъёмом при ховере, и если менять --tip-shift уже ВО ВРЕМЯ
   этого перехода (как было в первой версии правки — слушатель на
   pointerover/focusin), getBoundingClientRect() читает межкадровое,
   недоехавшее до конца значение transform, и сдвиг получается неверным
   (проверено вручную: 50px вместо нужных, потом 25px на том же круге).
   Сразу после вставки в DOM transition ещё не запускался (браузер не
   анимирует самое первое вычисление стиля нового узла) — значение
   транформа уже финальное для текущего --tip-shift (0px), измерение
   надёжно. */
const TIP_EDGE_MARGIN=8;
function fixTipOverflow(orb){
  const tip=orb.querySelector(':scope > .tip'); if(!tip) return;
  orb.style.removeProperty('--tip-shift');
  const rect=tip.getBoundingClientRect();
  let shift=0;
  if(rect.left<TIP_EDGE_MARGIN) shift=TIP_EDGE_MARGIN-rect.left;
  else if(rect.right>innerWidth-TIP_EDGE_MARGIN) shift=(innerWidth-TIP_EDGE_MARGIN)-rect.right;
  if(shift) orb.style.setProperty('--tip-shift', `${shift}px`);
}
function fixTipOverflowIn(container){
  container.querySelectorAll(':scope .orb').forEach(fixTipOverflow);
}
function fixTipOverflowEverywhere(){
  ['dashBody','colonies','colonyPage'].forEach(id=>{
    const el=document.getElementById(id); if(el) fixTipOverflowIn(el);
  });
}
// Изменение ширины окна меняет число колонок в сетке (flex-wrap) — какие
// круги крайние, тоже меняется. Пересчитываем по всем уже отрисованным
// местам разом; сам renderDash()/renderColonies() тут не трогаем (виды
// плана/колоний не пересобираются заново только из-за resize).
let tipOverflowResizeTimer=null;
window.addEventListener('resize', ()=>{
  clearTimeout(tipOverflowResizeTimer);
  tipOverflowResizeTimer=setTimeout(fixTipOverflowEverywhere, 150);
});
// Шрифты подключены с font-display:swap (см. @font-face выше) — до их
// подгрузки текст рисуется запасным шрифтом с другой шириной, и когда
// настоящий шрифт подменяет его, ширина топбара/карточек чуть меняется,
// а с ней и то, какой круг сетки крайний. Пересчитываем один раз, когда
// все шрифты странице точно готовы (обнаружено вручную при проверке
// этой самой правки: сразу после рендера отступ был верным, а через
// секунду-две — уже нет, из-за одной этой подмены шрифта).
document.fonts && document.fonts.ready.then(fixTipOverflowEverywhere);

/* Маленький круг структуры внутри колонии. */
function structOrb(kind,count,planetType,opts){
  opts=opts||{};
  // У фабрики (в отличие от экстрактора/причала/КЦ) содержимое разное
  // от пина к пину — та же постройка, но продукт свой (см. pin.product,
  // scripts/sync_colony_status.py::pin_detail()). Иконка постройки была
  // одинаковой для всех фабрик колонии и не отличала их друг от друга;
  // иконка продукта — то же самое отличие, что уже показано в детальной
  // панели пина (pinUnitsHTML), но и в компактной полосе (structOrbsForRow).
  const productIconId = opts.product ? ids[opts.product] : null;
  const id = productIconId
    || (kind==='command_center' ? ids[`${planetType} Command Center`] : ids['structure:'+kind]);
  const size=opts.size||46, R=(size/2)-2, r2=R-5;
  const cOut=2*Math.PI*R, cIn=2*Math.PI*r2;

  // Командный центр показывает две нагрузки сразу: CPU бирюзовым слева,
  // Power красным справа — как в игре. Штриховка делает их различимыми
  // даже когда обе почти полные.
  let outer;
  if(kind==='command_center' && opts.cpu!=null){
    const half=cOut/2;
    outer=`<circle cx="${size/2}" cy="${size/2}" r="${R}" stroke="var(--ring-extract)"
             stroke-dasharray="${half*opts.cpu/100} ${cOut}" stroke-width="2.5"/>
           <circle cx="${size/2}" cy="${size/2}" r="${R}" stroke="var(--alarm)"
             stroke-dasharray="0 ${half} ${half*opts.pg/100} ${cOut}" stroke-width="2.5"/>`;
  } else {
    outer=`<circle class="outer" cx="${size/2}" cy="${size/2}" r="${R}"
             stroke="var(${RING[kind]})" stroke-dasharray="${cOut} ${cOut}" stroke-width="2.5"/>`;
  }

  // Внутреннее кольцо — ход производственного цикла настоящей фабрики
  // (opts.cycle, 0-100, из last_cycle_start+cycle_minutes, см. orb()/
  // pinCycleInfo()). opts.idle — тот же факт, только цикл уже кончился, а
  // новый не начался (ждёт сырья): кольцо не пунктирное «неизвестно», а
  // полное белое и мигает — это и есть сигнал «требует внимания». Пунктир
  // остаётся для структур, у которых понятия цикла вообще нет (причал,
  // склад, командный центр, экстрактор) и для плана, где opts.cycle не
  // считается вовсе.
  const cyc = opts.cycle;
  let inner;
  if(opts.idle){
    inner = `<circle class="cycle idle" cx="${size/2}" cy="${size/2}" r="${r2}" stroke-dasharray="${cIn} ${cIn}"/>`;
  } else if(cyc==null){
    inner = `<circle class="cycle none" cx="${size/2}" cy="${size/2}" r="${r2}" stroke-dasharray="2 4"/>`;
  } else {
    inner = `<circle class="cycle" cx="${size/2}" cy="${size/2}" r="${r2}"
         stroke-dasharray="${cIn*cyc/100} ${cIn}" stroke-linecap="round"/>`;
  }

  return `<div class="orb${(cyc!=null&&!opts.idle)?' busy':''}" style="--orb:${size}px;cursor:default"
       title="${sname(kind)}${count>1?' ×'+count:''}${kind==='command_center'?' ('+planetType+')':''}${opts.idle?' · '+t('cycIdleSince'):''}">
    <div class="disc">${id?`<img src="${ICON(id)}" style="width:58%;height:58%;object-fit:contain">`:''}</div>
    <svg class="ring" viewBox="0 0 ${size} ${size}">
      <circle class="track" cx="${size/2}" cy="${size/2}" r="${R}"/>
      ${outer}${inner}
    </svg>
    ${count>1?`<div class="badge">×${count}</div>`:''}</div>`;
}

/* Разворачивает состав поштучно: 24 фабрики — это 24 круга, а не один
   со значком «×24». Так устроено игровое окно, и так видно каждый
   производственный модуль. Полоса переносится сама (flex-wrap). */
function expandStructures(p){
  const out=[];
  (p.structures_detail||[]).forEach(s=>{ for(let i=0;i<s.count;i++) out.push(s.kind); });
  return out;
}

/* Прогресс цикла ОДНОЙ настоящей фабрики — last_cycle_start+cycle_minutes,
   те же поля ESI, что и в панели колонии (structureUnitFields, isFactory).

   История вопроса. Первая версия (12.09.2026) досчитывала фазу цикла
   вперёд ПО МОДУЛЮ (now-last_cycle_start) mod cycle_minutes —
   предполагая, что фабрика крутится бесконечно. Это давало
   «В производстве» ДАЖЕ когда сырьё кончилось много циклов назад:
   остаток от деления всегда попадает в 0-100%, сколько бы времени ни
   прошло. Вторая версия (13.09.2026) убрала перенос по модулю, но
   сравнивала last_cycle_start с ТЕКУЩИМ временем (Date.now()) — и
   ошиблась в другую сторону: ESI пересчитывает last_cycle_start только
   при заходе в колонию в игровом клиенте (esi-issues #654, подтверждено
   исходниками стороннего PI-инструмента с открытым кодом), поэтому у
   ЛЮБОЙ колонии, которую давно не открывали, elapsed относительно
   «сейчас» почти всегда превышает cycle_minutes — простаивающими
   показывались практически все фабрики поголовно, включая настоящие
   работающие.

   Третья версия сравнивает last_cycle_start с `gameLastUpdate` —
   временем, когда ИГРА (не мы) в последний раз пересчитала эту колонию
   (ESI-поле last_update у /characters/{id}/planets/, Colony.
   game_last_update, см. sync_colony_status.py) — тот же приём разбора
   снимка, что и у стороннего инструмента: `activityState = lastCycleAgo
   < cycleTime`, где lastCycleAgo считается от `lastUpdate`, а не от
   текущего времени. Тот инструмент после этого ещё и симулирует колонию
   вперёд по маршрутам и буферам каждой фабрики — то, чего мы пока не
   делаем: у нас нет ни маршрутов между пинами, ни проверенных объёмов
   входа схемы. Поэтому вместо уверенного прогноза вперёд — честное
   третье состояние «неизвестно, в последний раз (при заходе в игру)
   работала».

   Состояния:
     cycle: 0-100, idle:false — на момент gameLastUpdate цикл ещё не
            истёк, и с тех пор прошло меньше cycle_minutes — с высокой
            уверенностью всё ещё в цикле, прогресс от last_cycle_start
            до сейчас.
     idle:true, idleMs — на момент gameLastUpdate цикл УЖЕ истёк, новый
            не начался: это подтверждённый факт (не проекция), простаивает
            idleMs = сейчас - (last_cycle_start+cycle_minutes).
     idle:false, unknownSince — на момент gameLastUpdate цикл ещё шёл, но
            с тех пор прошло больше cycle_minutes реального времени —
            дальше неизвестно, работает или встала (симуляции у нас нет).
     neverStarted:true — last_cycle_start нет вовсе.

   Четвёртая версия (13.09.2026) закрывает состояние «unknownSince»,
   когда для колонии есть маршруты (routes) и известны количества
   входа/выхода схемы (schematics, /api/initial-data): simulateColony-
   Factories() честно досчитывает буферы каждой фабрики вперёд по
   маршрутам, и его результат (simResult) передаётся сюда третьим
   параметром вызывающей стороной — pinCycleInfo просто отдаёт его как
   есть, без изменений. Без маршрутов (колония не пересинхронизирована
   этой версией, либо на планете их и правда нет) simResult не
   передаётся, и остаётся честное «неизвестно» третьей версии. */
function pinCycleInfo(pin, gameLastUpdate, simResult){
  if(!pin.kind || !pin.kind.endsWith('industry_facility')) return {cycle:null, idle:false};
  if(simResult) return simResult;
  if(!pin.cycle_minutes) return {cycle:null, idle:false, unknown:true};
  if(!pin.last_cycle_start) return {cycle:null, idle:true, neverStarted:true};
  const cycleMs=pin.cycle_minutes*60000;
  const startMs=Date.parse(pin.last_cycle_start);
  const refMs=gameLastUpdate?Date.parse(gameLastUpdate):Date.now();
  const elapsedAtSnapshot=Math.max(0, refMs-startMs);
  if(elapsedAtSnapshot>=cycleMs){
    // Подтверждено на момент снимка: цикл истёк, новый не начался.
    // Не «протухнет» обратно без нового last_cycle_start — можно смело
    // мерить простой от сейчас, а не от снимка.
    const idleMs=Math.max(0, Date.now()-(startMs+cycleMs));
    return {cycle:null, idle:true, idleMs};
  }
  const sinceSnapshot=Math.max(0, Date.now()-refMs);
  if(gameLastUpdate && sinceSnapshot>=cycleMs){
    // На снимке всё ещё шёл, но снимок сам уже старше одного цикла —
    // дальше без симуляции маршрутов/буферов честно не знаем.
    return {cycle:null, idle:false, unknownSince:gameLastUpdate};
  }
  const elapsedNow=Math.max(0, Date.now()-startMs);
  return {cycle:100*Math.min(elapsedNow,cycleMs)/cycleMs, idle:false, remainingMs:cycleMs-elapsedNow};
}

/* ── Проекция состояния фабрик по маршрутам ────────────────────────
   pinCycleInfo() честно останавливается на «неизвестно с момента
   снимка» (gameLastUpdate), когда с последнего захода в колонию прошло
   больше одного цикла фабрики: last_cycle_start сам по себе больше
   ничего не говорит, а настоящую работу колонии в игре решает, докуда
   доходит сырьё от экстрактора и сколько его накопилось у каждой
   фабрики — то есть маршруты (routes) и буферы (contents). Раньше
   этого не считали вовсе: колония с рабочим экстрактором и частью
   работающих фабрик показывала «все простаивают» (пользователь прислал
   скриншот из другого PI-инструмента 13.09.2026: 7 из 9 фабрик
   «In production», 2 кратко «Waiting for resources, Idle for 36s» — у
   нас все 9 давно «истекший цикл»).

   Идея (по образцу стороннего PI-инструмента с открытым кодом): взять
   снимок на момент gameLastUpdate (buf. каждого пина = pin.contents на
   тот момент — оно ТОЖЕ достоверно только на этот момент, по той же
   причине, что и last_cycle_start) и прогнать события вперёд до
   «сейчас»: экстрактор
   каждый cycle_seconds выдаёт decayed-количество (extractorDecayedQty,
   тот же факт игровой механики, что и в панели экстрактора) и рассылает
   его по исходящим маршрутам; фабрика каждый cycle_minutes проверяет,
   хватает ли в её буфере входов схемы (data/schematics.json —
   количества, извлечённые из проверенных игровых шаблонов, правило 2,
   НЕ выдумка), и либо потребляет и производит, либо простаивает.
   Склад/причал/КЦ — просто проходной буфер (маршрут внутрь сразу
   пробрасывается на исходящие маршруты, у склада нет своего цикла).

   Сознательные упрощения (честно, а не молча):
   - Ёмкость склада/причала в кубометрах НЕ ограничивает поток —
     на фронтенде нет объёма каждого товара (это серверный расчёт
     used_m3), только сами count'ы. Настоящее «склад забит» уже отдельно
     показывает isLaunchpadFull() по последнему синку — здесь это не
     дублируется.
   - Порядок раздачи между несколькими маршрутами с одним источником —
     по порядку самих routes от ESI, не по «приоритету» в игровом UI
     (ESI его не отдаёт). Расходится с игрой только в редком случае
     конкуренции нескольких потребителей за один дефицитный ресурс.
   - Возвращает null, если считать нечем (нет маршрутов, нет
     data/schematics.json, нет gameLastUpdate) — тогда вызывающая
     сторона просто не передаёт simResult, и pinCycleInfo честно
     остаётся на «неизвестно». */
function simulateColonyFactories(pins, routes, gameLastUpdateIso){
  if(!gameLastUpdateIso || !routes || !routes.length || !pins || !pins.length) return null;
  const startMs=Date.parse(gameLastUpdateIso);
  const nowMs=Date.now();
  if(!(nowMs>startMs)) return null;

  const typeIdOf=name=>name!=null?ids[name]:null;
  const st=new Map();
  let anyFactory=false;
  for(const pin of pins){
    if(pin.pin_id==null) continue;
    const buf=new Map();
    (pin.contents||[]).forEach(c=>{
      const tid=typeIdOf(c.name); if(tid==null) return;
      buf.set(tid,(buf.get(tid)||0)+c.amount);
    });
    const isFactory=pin.kind&&pin.kind.endsWith('industry_facility');
    const isExtractor=pin.kind==='extractor_control_unit';
    const entry={pin, buf, isFactory, isExtractor, isStorage:!isFactory&&!isExtractor, active:false, idleSinceMs:null};
    if(isFactory){
      const outId=typeIdOf(pin.product);
      const schem=(outId!=null&&schematics[String(outId)])||null;
      if(schem && pin.cycle_minutes && pin.last_cycle_start){
        anyFactory=true;
        entry.cycleMs=pin.cycle_minutes*60000;
        entry.outputTypeId=outId; entry.outputQty=schem.output_qty;
        entry.demands=new Map(Object.entries(schem.inputs).map(([k,v])=>[Number(k),v]));
        entry.lastRun=Date.parse(pin.last_cycle_start);
      } else {
        entry.unsupported=true;
      }
    } else if(isExtractor){
      entry.cycleMs=pin.cycle_seconds!=null?pin.cycle_seconds*1000:null;
      entry.installIso=pin.install_time||null;
      entry.expiryMs=pin.expiry_time?Date.parse(pin.expiry_time):null;
      entry.baseValue=pin.qty_per_cycle;
      entry.outputTypeId=pin.product_type_id;
      // У экстрактора (в отличие от фабрики) last_cycle_start вообще не
      // сохраняется отдельным полем (pin_detail() кладёт его только
      // фабрикам) — планировать события нужно от install_time: программа
      // экстрактора крутит циклы с момента установки, а не с момента
      // последнего визита в колонию (в отличие от last_cycle_start,
      // install_time не привязан к визитам, ESI отдаёт его как есть).
      // Раньше здесь стояло pin.last_cycle_start, которого у экстрактора
      // никогда нет — entry.lastRun оставался null, extractor ни разу не
      // планировался в событийном цикле, ни грамма сырья не долетало ни
      // до одной фабрики, и результат был тем же самым «все простаивают»,
      // который эта симуляция должна была устранить (обнаружено
      // 13.09.2026 на реальных данных прода).
      entry.lastRun=entry.installIso?Date.parse(entry.installIso):null;
    }
    st.set(pin.pin_id, entry);
  }
  if(!anyFactory) return null;

  const outRoutes=new Map(), inRoutes=new Map();
  routes.forEach(r=>{
    const typeId=typeIdOf(r.product);
    if(typeId==null||r.source_pin_id==null||r.destination_pin_id==null) return;
    if(!st.has(r.source_pin_id)||!st.has(r.destination_pin_id)) return;
    if(!outRoutes.has(r.source_pin_id)) outRoutes.set(r.source_pin_id,[]);
    outRoutes.get(r.source_pin_id).push({dest:r.destination_pin_id, typeId, qty:r.quantity||0});
    if(!inRoutes.has(r.destination_pin_id)) inRoutes.set(r.destination_pin_id,[]);
    inRoutes.get(r.destination_pin_id).push({src:r.source_pin_id, typeId, qty:r.quantity||0});
  });

  const canAccept=(entry, typeId, qty)=>{
    if(entry.isFactory){
      if(!entry.demands || !entry.demands.has(typeId)) return 0;
      const need=entry.demands.get(typeId), have=entry.buf.get(typeId)||0;
      return Math.max(0, Math.min(qty, need-have));
    }
    return qty; // склад/причал/КЦ — без ограничения по объёму, см. докстринг
  };

  let events=[]; // {pinId,t} — линейный поиск минимума: пинов в колонии мало
  // Горизонт расписания — далеко за «сейчас» (раньше останавливался
  // ровно на nowMs): депletion причала теперь ищется тем же самым точным
  // событийным движком, что и «сейчас»-состояние ниже, а не отдельной
  // формулой «остаток/скорость» — см. подробности у lastIdleTransition и
  // results.depletionMs в конце функции. 90 дней — дальше уже не всё
  // равно, и MAX_EVENTS ниже в любом случае оборвёт раньше на настоящих
  // колониях с их обычным числом пинов.
  const HORIZON_MS=nowMs+1000*60*60*24*90;
  const schedule=(pinId,t)=>{
    if(t==null||t>HORIZON_MS) return;
    const existing=events.find(e=>e.pinId===pinId);
    if(existing){ if(t<existing.t) existing.t=t; return; }
    events.push({pinId,t});
  };

  const push=(sourcePinId, typeId, qty, t, depth)=>{
    if(qty<=0 || depth>pins.length) return; // depth — защита от кольцевых маршрутов
    let remaining=qty;
    for(const r of (outRoutes.get(sourcePinId)||[])){
      if(r.typeId!==typeId || remaining<=0) continue;
      const dest=st.get(r.dest);
      const amt=Math.min(remaining, r.qty, canAccept(dest, typeId, remaining));
      if(amt<=0) continue;
      dest.buf.set(typeId,(dest.buf.get(typeId)||0)+amt);
      remaining-=amt;
      if(dest.isStorage){
        push(r.dest, typeId, amt, t, depth+1); // проходной буфер — сразу дальше
      } else if(dest.isFactory && !dest.unsupported){
        schedule(r.dest, t); // могло хватить входов — попробовать прямо сейчас
      }
    }
  };

  for(const [pinId,e] of st){
    if((e.isExtractor||e.isFactory) && !e.unsupported && e.lastRun!=null && e.cycleMs){
      schedule(pinId, e.lastRun+e.cycleMs);
    }
  }

  // typeId -> имя продукта — обратное к ids (имя -> id), нужно только
  // здесь, чтобы отрисовать buf() из симуляции тем же форматом
  // {name, amount}, что и настоящий pin.contents (contents ESI приходит
  // с именем, уже резолвленным сервером).
  const nameOfType=new Map();
  for(const name in ids) if(typeof ids[name]==='number') nameOfType.set(ids[name], name);

  // Снимок «сейчас» — состояние st на тот момент событийной ленты, когда
  // она впервые переходит через nowMs (до этого момента состояние ещё
  // совпадает с настоящим снимком ESI + тем, что дальше досчитала
  // симуляция). Вызывается ровно один раз, см. ниже.
  const snapshotResults=()=>{
    const snap=new Map();
    for(const [pinId,entry] of st){
      if(entry.isFactory && !entry.unsupported){
        if(entry.active){
          const elapsed=Math.max(0, nowMs-entry.lastRun);
          snap.set(pinId, {cycle:100*Math.min(elapsed,entry.cycleMs)/entry.cycleMs, idle:false,
            remainingMs:entry.cycleMs-Math.min(elapsed,entry.cycleMs)});
        } else {
          snap.set(pinId, {cycle:null, idle:true,
            idleMs:Math.max(0, nowMs-(entry.idleSinceMs!=null?entry.idleSinceMs:entry.lastRun))});
        }
      } else if(entry.isStorage){
        // Проекция содержимого причала/склада/хранилища КЦ на «сейчас» —
        // тот же buf, что расходуют фабрики, а не статичный снимок ESI с
        // последней синхронизации: раньше список содержимого в панели
        // «Детали» не менялся между синхронизациями вовсе (сырьё не
        // убывало на глазах), а готовая продукция, ожидающая вывоза, в
        // нём не появлялась совсем, даже когда маршрут для неё есть
        // (найдено пользователем 17.09.2026). Содержит и остаток сырья,
        // и уже произведённый, но ещё не вывезенный продукт, если для
        // него задан исходящий маршрут сюда.
        const contents=[];
        // used_m3 по проекции, не по снимку: состав со временем сдвигается
        // от сырья к готовой продукции, а объём единицы у них обычно
        // разный (найдено пользователем 17.09.2026: заполненность в
        // интерфейсе стояла на месте, хотя реальный объём в игре заметно
        // падал по мере убывания «тяжёлого» сырья). typeVolumes —
        // /api/initial-data, тот же объём на единицу, которым сервер
        // считает used_m3 из настоящего снимка (scripts/sync_colony_status
        // ::type_volume()). Честно null, если объём хоть одного предмета
        // не узнать — частичная сумма выглядела бы точнее, чем есть.
        let usedM3=0, volumeKnown=true;
        entry.buf.forEach((amount,typeId)=>{
          if(amount<0.5) return; // меньше единицы — то же самое, что «ничего»
          contents.push({name:nameOfType.get(typeId)||null, type_id:typeId, amount:Math.round(amount)});
          const vol=typeVolumes[String(typeId)];
          if(vol==null){ volumeKnown=false; return; }
          usedM3+=vol*amount;
        });
        snap.set(pinId, {contents, usedM3:volumeKnown?Math.round(usedM3*100)/100:null});
      }
    }
    return snap;
  };

  let results=null;
  // Момент, когда ВСТАЛА ПОСЛЕДНЯЯ (не первая) из фабрик, до этого
  // работавших — решение пользователя (17.09.2026), после разбора
  // реального случая: 12 фабрик на одном причале, при частичной нехватке
  // (остатка хватает не на все сразу) бо́льшая часть фабрик всё равно
  // получает своё и работает ещё цикл, прежде чем причал закончится
  // окончательно. «Первая фабрика встала» занижало бы время — колония
  // не «встала», просто временно недосчиталась одной-двух фабрик.
  // Каждое присвоение — событие t, обрабатываемое строго по возрастанию
  // (события всегда выбираются как минимум из очереди), поэтому
  // последнее присвоение за весь проход и есть истинный максимум.
  // Ограничение (честно, не молчком): если в колонии несколько НЕЗАВИСИМЫХ
  // причалов/цепочек, встаёт последняя из них — общая цифра для колонии,
  // не по каждой цепочке отдельно, см. docstring выше про несколько
  // (причал, вид сырья) пар.
  //
  // ИСПРАВЛЕНО (17.09.2026, найдено пользователем на реальных данных:
  // колония уже закончила переработку — часть фабрик отработала и
  // остановилась, часть вообще ни разу не запускалась — но в сводке
  // «N простаивает» не считалась). Раньше присвоение было под условием
  // `t>nowMs` — «встала» засчитывалось, только если это произошло уже
  // ПОСЛЕ снимка, в спроецированном будущем. Если же причал опустел ещё
  // ДО снимка (t<=nowMs — фабрики встали в прошлом, а не встанут в
  // будущем), присвоение не происходило вовсе, и depletionMs оставался
  // null — «неизвестно», хотя колония на самом деле уже полностью
  // истощена. Условие `t>nowMs` убрано: присвоение теперь безусловно на
  // каждом переходе «работала → встала», независимо от того, в прошлом
  // это событие относительно снимка или в будущем — то же самое
  // «последнее присвоение и есть истинный максимум» (переход, случившийся
  // раньше, но позже уже не отменяемый, потому что после него до конца
  // прохода очередь событий просто опустела — иначе цикл обработал бы
  // событие возобновления и присвоил бы заново). depletionMs в этом
  // случае получается ОТРИЦАТЕЛЬНЫМ — «истощилось NN назад», а не «через
  // NN» — везде далее по коду (fmtLeft(), isColonyIdle()) `<=0` уже и так
  // означает «кончилось», отрицательное значение не требует отдельной
  // обработки.
  let lastIdleTransition=null;
  // Фабрика хоть раз успешно набрала цикл В ЭТОМ прогоне симуляции —
  // нужно для ВТОРОГО случая того же бага (найдено пользователем
  // 17.09.2026, сразу следом за первым): если причал УЖЕ был пуст на
  // момент снимка (buf с нуля, не «опустел в ходе проекции»), ни одна
  // фабрика ни разу не переходит active:true→false — переходить не из
  // чего, entry.active всю симуляцию остаётся тем же false, с которого
  // стартовала. lastIdleTransition в такой колонии никогда не
  // присвоится, а колония на самом деле простаивает как минимум с
  // момента снимка. См. проверку после цикла событий ниже.
  let anyFactoryEverActive=false;
  let iterations=0;
  const MAX_EVENTS=50000; // защита от зависания на аномальных данных
  while(events.length && iterations++<MAX_EVENTS){
    let idx=0;
    for(let i=1;i<events.length;i++) if(events[i].t<events[idx].t) idx=i;
    const {pinId,t}=events[idx];

    // Первое событие, шагнувшее за «сейчас» — состояние ДО его обработки
    // и есть настоящее «сейчас»-состояние (события с t<=nowMs уже все
    // применены, это ещё нет). Дальше события продолжают обрабатываться
    // тем же движком — уже как проекция в будущее, только для поиска
    // depletionMs (см. ниже), возвращаемые results это не меняет: они
    // уже сняты.
    if(results===null && t>nowMs) results=snapshotResults();

    events.splice(idx,1);
    const entry=st.get(pinId);

    if(entry.isExtractor){
      const stillActive=entry.expiryMs==null||t<entry.expiryMs;
      if(stillActive && entry.baseValue!=null && entry.installIso && entry.outputTypeId!=null){
        const qty=extractorDecayedQty(entry.baseValue, entry.installIso, entry.pin.cycle_seconds, t);
        if(qty) push(pinId, entry.outputTypeId, qty, t, 0);
      }
      entry.lastRun=t;
      if(stillActive) schedule(pinId, t+entry.cycleMs);
      continue;
    }

    // Забрать то, что накопилось на складах, маршрутизированных сюда —
    // покрывает и запас, лежавший на складе ЕЩЁ ДО начала проекции, и
    // пополнение буфера фабрики под следующий цикл (см. вызов ниже).
    const pullFromStorage=()=>{
      for(const r of (inRoutes.get(pinId)||[])){
        const src=st.get(r.src);
        if(!src || !src.isStorage) continue;
        const have=src.buf.get(r.typeId)||0;
        if(have<=0) continue;
        const amt=Math.min(have, r.qty, canAccept(entry, r.typeId, have));
        if(amt<=0) continue;
        src.buf.set(r.typeId, have-amt);
        entry.buf.set(r.typeId, (entry.buf.get(r.typeId)||0)+amt);
      }
    };
    pullFromStorage();

    const wasActive=entry.active;
    let canConsume=true;
    for(const [typeId,need] of entry.demands){
      if((entry.buf.get(typeId)||0)<need){ canConsume=false; break; }
    }
    if(canConsume){
      for(const [typeId,need] of entry.demands) entry.buf.set(typeId, entry.buf.get(typeId)-need);
      entry.active=true; entry.idleSinceMs=null; entry.lastRun=t;
      anyFactoryEverActive=true;
      push(pinId, entry.outputTypeId, entry.outputQty, t, 0);
      // Игра резервирует вход СЛЕДУЮЩЕГО цикла в момент, когда кончился
      // текущий (найдено 17.09.2026 на реальных данных прода — у каждой
      // фабрики уже был предзагружен ровно один цикл сырья в её
      // собственном buf на момент синхронизации, а склад расходовался
      // только на цикл ПОЗЖЕ, чем должен был: 12 фабрик × 40 ед. лишних
      // на каждый вид сырья — ровно один пропущенный «забор» со склада
      // на весь горизонт проекции). Без этого повторного вызова забор
      // происходил бы только на СЛЕДУЮЩЕМ событии этой фабрики (через
      // целый cycleMs) — склад в проекции пустел на цикл позже
      // настоящего, и заодно расход по складу отставал от уже
      // учтённого выпуска продукции на тот же один цикл.
      pullFromStorage();
      schedule(pinId, t+entry.cycleMs);
    } else {
      entry.active=false;
      if(entry.idleSinceMs==null) entry.idleSinceMs=t;
      // Переход «работала — встала» — запоминаем каждый такой переход,
      // не только первый и не только в будущем (см. правку 17.09.2026
      // выше): события идут строго по возрастанию t, поэтому последнее
      // присвоение к концу прохода и есть момент, когда встала
      // ПОСЛЕДНЯЯ из работавших фабрик (то есть колония в целом), см.
      // results.depletionMs ниже.
      if(wasActive) lastIdleTransition=t;
      // Не перепланируем сами — разбудит push() при следующей поставке.
    }
  }
  if(results===null) results=snapshotResults(); // весь горизонт уместился до «сейчас» — редкий случай (колонию давно не синхронизировали)

  // Время до исчерпания сырья в причале (только для переработки — у
  // добычи буфер пополняет сам экстрактор, у переработки его пополняет
  // только игрок вручную привозом, значит запас конечный и ничем сам не
  // растёт).
  //
  // РАНЬШЕ считалось формулой «остаток / расход в час» — непрерывным
  // приближением поверх ПО СУТИ дискретного потребления: фабрика
  // забирает вход целиком в момент старта цикла, а не размазывает
  // расход по всей его длительности, поэтому формула занижала время —
  // последний цикл, на старт которого сырья ещё хватило, в игре всё
  // равно докручивается до конца (пользователь заметил на практике
  // 17.09.2026: «время почти точное, но показывает окончание на час
  // раньше — как будто пропускает тот цикл фабрик, когда сырья в
  // причале уже не будет, но фабрики уйдут на последний цикл»).
  //
  // ТЕПЕРЬ это не формула, а прямое чтение из того же точного событийного
  // движка, что считает «сейчас»-состояние выше.
  //
  // ПЕРЕСМОТРЕНО ЕЩЁ РАЗ В ТОТ ЖЕ ДЕНЬ (17.09.2026, решение
  // пользователя после разбора реального случая: 12 фабрик на одном
  // причале). Первая версия брала момент, когда ПЕРВАЯ работавшая
  // фабрика не набрала полный цикл — но при частичной нехватке (остатка
  // хватает не всем сразу) это занижает время: причал ещё способен
  // прокормить большинство фабрик ещё один полный цикл, колония не
  // «встала», просто на секунду недосчиталась одной-двух фабрик из
  // двенадцати. Теперь берётся момент, когда встала ПОСЛЕДНЯЯ из
  // работавших — то есть когда причал перестаёт кормить вообще кого бы
  // то ни было, а не первого пострадавшего. При нескольких НЕЗАВИСИМЫХ
  // причалах/цепочках в одной колонии это означает «когда встанет самая
  // живучая из них», не «когда любая из них» — честное ограничение,
  // отдельный учёт по каждой цепочке потребовал бы знать заранее, какие
  // факторы вообще связаны общим источником, а не просто общей колонией.
  // ВТОРОЙ случай того же бага (найдено пользователем 17.09.2026, сразу
  // следом за первым): причал был УЖЕ пуст на момент снимка (не опустел
  // В ХОДЕ проекции, а пришёл пустым) — ни одна фабрика ни разу не
  // получает канал «работала → встала» внутри симуляции, потому что
  // работать ей не с чего было ни на одном шаге, entry.active никогда
  // не становится true. lastIdleTransition в этом случае так и остаётся
  // null, хотя колония совершенно точно простаивает — переход просто не
  // наблюдаем изнутри симуляции, а не «неизвестно». Честная нижняя
  // граница: раз ни одна фабрика не работала ни одного мгновения за весь
  // горизонт от снимка (anyFactoryEverActive=false), простаивает как
  // минимум с самого снимка (раньше снимка не знаем — не гадаем).
  if(lastIdleTransition==null && anyFactory && !anyFactoryEverActive) lastIdleTransition=startMs;
  results.depletionMs = lastIdleTransition!=null ? lastIdleTransition-nowMs : null;
  return results;
}

/* Колония простаивает — общее определение для сводки «N простаивает»
   (см. renderColonies()), одно на все три вида (детали/список/сетка),
   а не свой счёт в каждом: у добычи это «нет запущенной программы
   экстрактора, или её время уже истекло» (nearest_expiry), у
   переработки — «ВСЕ фабрики истекший цикл не сменили новым»
   (pinCycleInfo(...).idle, см. её докстринг). Без данных о пинах
   переработку честно не считаем ни простаивающей, ни работающей —
   выдумывать нечем.
   **Пересмотрено в тот же день (13.09.2026).** Короткая правка выше
   добавляла для добычи ещё и «все фабрики простаивают» — по мотивам
   скриншота колонии, где рабочий экстрактор соседствовал с двумя
   фабриками без цикла больше суток. Пользователь показал ВТОРОЙ
   скриншот (из другого PI-инструмента, актуальное состояние той же
   связки «добываю и тут же перерабатываю»): 7 из 9 фабрик
   «In production», 2 — «Waiting for
   resources, Idle for 36s». Это НЕ поломка, а нормальное
   самокорректирующееся поведение недоснабжённой колонии: добыча не
   поспевает прокормить все фабрики сразу, часть из них периодически
   простаивает и возобновляется по мере накопления сырья. Причина этого
   простоя — недостаточная скорость добычи, а это уже отдельный, ранее
   реализованный сигнал (`isColonyDeficient()`/`DEFICIT_UNITS_PER_HOUR`,
   см. Фазу 7), а не «колония встала». Смешивать его с «простаивает» —
   значит либо задваивать один и тот же факт под двумя ярлыками, либо
   ложно помечать «простаивает» колонию, которая на самом деле работает
   с перебоями (именно то, что показывает второй скриншот). Правка
   отменена: у добычи снова только `nearest_expiry`, как было до
   PR #56.
   **Исправлено 18.09.2026 (тот же класс бага, что и depletionMs у
   переработки, найден пользователем на реальных данных: десятки
   колоний с бейджем «программа завершена» не считались простаивающими
   вовсе).** `nearest_expiry` — это `expiry_time` последней программы
   экстрактора с ESI, и игра НЕ обнуляет его, когда время истекает —
   поле остаётся тем же прошедшим моментом, пока игрок не перезапустит
   программу вручную. `!p.nearest_expiry` ловит только редкий случай
   «пинов экстрактора нет вовсе», а не куда более частый «таймер есть,
   но уже в прошлом» — ровно то же самое различие, что чинили для
   depletionMs. Теперь сверяется с текущим временем, той же проверкой
   `<=0`, что и у бейджа (fmtBadgeLeft/fmtLeft), — а не только с
   отсутствием поля. */
function isColonyIdle(p){
  if(p.role_key==='mine')
    return !p.nearest_expiry || Date.parse(p.nearest_expiry)<=Date.now();
  if(p.role_key==='proc'){
    // Переопределено 16.09.2026 (docs/ROADMAP.md, Фаза 9): раньше —
    // «ВСЕ фабрики находятся в состоянии idle по pinCycleInfo()», прокси
    // на честный сигнал, которого тогда не было. Теперь честный сигнал
    // есть — sim.depletionMs (время до исчерпания сырья в причале,
    // simulateColonyFactories()) — и «простаивает» означает буквально
    // «сырьё в причале уже кончилось» (depletionMs<=0), а не то, что все
    // фабрики синхронно не крутят цикл (это могло быть верно и при живом
    // снабжении — просто фаза цикла у всех совпала).
    const factories=(p.pins||[]).filter(pin=>pin.kind&&pin.kind.endsWith('industry_facility'));
    if(!factories.length) return false;
    const sim=simulateColonyFactories(p.pins,p.routes,p.game_last_update);
    return !!(sim && sim.depletionMs!=null && sim.depletionMs<=0);
  }
  return false;
}

/* Порядок пинов в детальном виде факта из игры: командный центр,
   экстрактор (если есть), причал, хранилище (если есть), фабрики —
   по просьбе пользователя, отдельно от STRUCTURE_ORDER расчётного плана
   (там порядок другой, менять не просили). Сортировка устойчивая: пины
   одного вида (несколько фабрик) сохраняют исходный порядок ESI между
   собой — реальный порядок циклов ни на что не влияет, важна только
   группировка по виду. */
const REAL_PIN_ORDER=['command_center','extractor_control_unit','launchpad','storage_facility',
  'basic_industry_facility','advanced_industry_facility','high_tech_industry_facility'];
function sortPinsForDisplay(pins){
  return pins.map((pin,i)=>({pin,i})).sort((a,b)=>{
    const ra=REAL_PIN_ORDER.indexOf(a.pin.kind), rb=REAL_PIN_ORDER.indexOf(b.pin.kind);
    return (ra<0?REAL_PIN_ORDER.length:ra)-(rb<0?REAL_PIN_ORDER.length:rb) || a.i-b.i;
  }).map(x=>x.pin);
}

/* Полоса иконок структур под карточкой колонии (colonyCardHTML). У
   настоящей колонии с пересинхронизированными пинами (p.pins) — каждый
   пин своим кругом с настоящим циклом (см. pinCycleInfo) и настоящей
   загрузкой командного центра (p.cpu_percent/pg_percent, см. realRows());
   иначе (план, ещё не пересинхронизированный факт) — прежний агрегат
   structures_detail без прогресса. */
function structOrbsForRow(p){
  if(p.isReal && p.pins && p.pins.length){
    const sim=simulateColonyFactories(p.pins,p.routes,p.game_last_update);
    return sortPinsForDisplay(p.pins).map(pin=>{
      const info=pinCycleInfo(pin,p.game_last_update,sim&&sim.get(pin.pin_id));
      const opts={cycle:info.cycle, idle:info.idle};
      if(pin.kind==='command_center' && p.cpu_percent!=null){
        opts.cpu=p.cpu_percent; opts.pg=p.pg_percent;
      }
      if(pin.kind.endsWith('industry_facility') && pin.product) opts.product=pin.product;
      return structOrb(pin.kind,1,p.planet_type,opts);
    }).join('');
  }
  return expandStructures(p).map(kind=>{
    if(kind==='command_center'){
      if(!p.isReal) return structOrb(kind,1,p.planet_type,{cpu:p.cpu_percent,pg:p.pg_percent});
      if(p.cpu_percent!=null) return structOrb(kind,1,p.planet_type,{cpu:p.cpu_percent,pg:p.pg_percent});
    }
    // Расчётный план: колония гонит один продукт (p.res_out) через ВСЕ
    // свои фабрики разом — в отличие от настоящей колонии, где у каждого
    // пина может быть свой (см. structOrb/opts.product), здесь один и тот
    // же res_out ставится на каждую фабричную иконку строки.
    if(kind.endsWith('industry_facility') && !p.isReal && p.res_out){
      return structOrb(kind,1,p.planet_type,{product:p.res_out});
    }
    return structOrb(kind,1,p.planet_type,{});
  }).join('');
}

function secColor(t){ // цвет статуса безопасности как в игре
  return t==='Barren'||t==='Temperate' ? '#5b8a5b' : '#8a5b5b';
}

function sorted(){
  const c=[...plan];
  if(sortBy==='load')    c.sort((a,b)=>Math.max(b.cpu_percent,b.pg_percent)-Math.max(a.cpu_percent,a.pg_percent));
  if(sortBy==='reserve') c.sort((a,b)=>Math.max(a.cpu_percent,a.pg_percent)-Math.max(b.cpu_percent,b.pg_percent));
  if(sortBy==='pilot')   c.sort((a,b)=>a.character.localeCompare(b.character)||a.system.localeCompare(b.system));
  if(sortBy==='system')  c.sort((a,b)=>a.system.localeCompare(b.system)||(+a.planet)-(+b.planet));
  return c;
}

/* Правая плашка колонии: у плана — загрузка CPU/PG, у факта из игры —
   роль (добыча/переработка) диктует, ЧЕЙ цикл показывать справа.
   Фабричный цикл ESI не отдаёт вовсе (см. cycNoFactory) — показывается
   честное «нет данных», а не выдуманное время. */
function colonyTail(p){
  if(!p.isReal){
    const peak=Math.max(p.cpu_percent,p.pg_percent);
    return {lbl:t('loadLbl'), cls:peak>=90?' hot':'',
      val:`CPU ${p.cpu_percent.toFixed(1)}% · PG ${p.pg_percent.toFixed(1)}%`};
  }
  const exp=p.nearest_expiry?Date.parse(p.nearest_expiry):null;
  if(p.role_key==='mine'){
    const left=exp!=null?exp-Date.now():null;
    const cls=left==null?'':left<=0?' over':left<7200000?' soon':'';
    return {lbl:t('progEnds'), cls, exp, val:left==null?t('cycIdle'):fmtLeft(left)};
  }
  if(p.role_key==='proc'){
    // Честный расчёт (не догадка): sim.depletionMs — момент, когда первая
    // же фабрика встанет из-за нехватки сырья в причале, от того же
    // событийного движка simulateColonyFactories(), null если считать
    // нечем (нет маршрутов/схем/снимка) — тогда как и раньше «нет данных».
    const sim=simulateColonyFactories(p.pins,p.routes,p.game_last_update);
    const depletionMs=sim&&sim.depletionMs;
    if(depletionMs!=null){
      const cls=depletionMs<=0?' over':depletionMs<7200000?' soon':'';
      // exp — тот же приём, что и у добычи (nearest_expiry): абсолютная
      // метка времени, а не относительный остаток, чтобы плашка тикала
      // сама по setInterval (см. его докстринг), не перезапуская всю
      // симуляцию каждую минуту ради одного числа.
      return {lbl:t('procDepletionLbl'), cls, exp:Date.now()+depletionMs, val:fmtLeft(depletionMs)};
    }
    return {lbl:t('procCycleLbl'), cls:'', val:t('noData'), title:t('cycNoFactory')};
  }
  return {lbl:t('progEnds'), cls:'', val:t('cycIdle')};
}

/* Карточка колонии — общая для плана и «Моих колоний в игре»: та же
   разметка .colony/.colony-head/.strip, разница только в бейдже роли
   (у факта он не всегда известен) и в правой плашке (colonyTail). */
function colonyCardHTML(p){
  const tail=colonyTail(p);
  const roleBadge=p.role_key?`<span class="rolebadge ${roleClass(p)}">${roleLabel(p)}</span>`:'';
  const subExtra=p.isReal
    ? `<span class="synced">${t('syncedColony')}${p.upgrade_level?` · CC${p.upgrade_level}`:''} · ${pl(p.num_pins,'pPin')}</span>`
    : `<span class="num">${nloc(Math.round(p.planet_radius_km))} ${t('km')}</span>`;
  const detailsBtn = `<button class="details-btn" onclick="openColony('${p.isReal?p.matchId:p.id}')">${t('details')}</button>`;
  return `<div class="colony">
    <div class="colony-head">
      ${orb(p,54,false)}
      <div class="pilot-lg" title="${p.character}">${initials(p.character)}${avatarImg(charIdOf(p.character))}</div>
      <div>
        <div class="pilot-name">${p.character}</div>
        <div class="sub">
          ${roleBadge}
          <span class="sysbadge">${p.system} ${romanNumeral(p.planet)}</span>
          <span class="sec" style="color:${secColor(p.planet_type)}">${p.planet_type}</span>
          ${subExtra}
        </div>
      </div>
      <div class="tail">
        <div class="lbl">${tail.lbl}</div>
        <div class="val${tail.cls}"${tail.exp!=null?` data-exp="${tail.exp}"`:''}${tail.title?` title="${tail.title}"`:''}>${tail.val}</div>
      </div>
      ${detailsBtn}
    </div>
    <div class="strip">${structOrbsForRow(p)}</div>
  </div>`;
}

function renderDash(){
  const box=document.getElementById('dashBody');
  const jumpBtn=document.getElementById('jumpPlan');
  if(!plan.length){
    box.innerHTML=`<div class="empty">${t('noPlan')}</div>`;
    // Пустая строка, не «план не построен» — тот же факт уже сказан
    // прямо под этой панелью инструментов (#dashBody выше), повторять
    // его ещё раз в одной строке с кнопками экспорта незачем
    // (18.09.2026, по прямому запросу пользователя).
    document.getElementById('counts').innerHTML='';
    if(jumpBtn) jumpBtn.style.display='none';
    return;
  }
  if(jumpBtn) jumpBtn.style.display='';
  const rows=sorted();

  if(view==='details'){
    box.innerHTML=rows.map(colonyCardHTML).join('');
  }

  if(view==='list'){
    const byPilot={};
    rows.forEach(p=>(byPilot[p.character]=byPilot[p.character]||[]).push(p));
    box.innerHTML=Object.entries(byPilot).map(([name,items])=>`
      <div class="pilot-row">
        <div class="who" title="${esc(name)} · ${pl(items.length,'pColony')}">
          <div class="pilot-lg" style="width:52px;height:52px;font-size:16px">${initials(name)}${avatarImg(charIdOf(name))}</div>
        </div>
        <div class="orbs">${items.map(p=>orb(p,52,false)).join('')}</div>
      </div>`).join('');
  }

  if(view==='grid'){
    box.innerHTML=`<div class="grid">${rows.map(p=>orb(p,58)).join('')}</div>`;
  }

  const strained=plan.filter(p=>Math.max(p.cpu_percent,p.pg_percent)>=90).length;
  document.getElementById('counts').innerHTML=
    `<b>${pl(plan.length,'pColony')}</b>`
    + (strained?` · <span class="idle">${strained} ${t('noReserve')}</span>`:` · ${t('reserveOk')}`);
  fixTipOverflowIn(box);
}

/* ── Пороговые радиусы ────────────────────────────────────────
   Показываются ДО расчёта, при выборе домашней системы: иначе
   пользователь узнаёт о том, что планеты слишком крупные, только
   после построения плана, потратив время впустую. */
let thresholds=null, systemPlanets=[];

async function loadThresholds(){
  const ccu=Math.max(0,...crew.map(c=>c.ccu||0));
  try{
    const d=await (await fetch(`${API}/thresholds/${ccu}`)).json();
    thresholds=d.status==='success'?d.thresholds:null;
  }catch(e){ thresholds=null; }
  renderThresholds();
}

function renderThresholds(){
  const box=document.getElementById('thresholdNote'); if(!box) return;
  const system=document.getElementById('factorySys').value;
  if(!thresholds || !system){ box.innerHTML=''; return; }

  const limit=thresholds.p2p3_2factory;
  const limitP4=thresholds.p4_2factory;
  const fitting=systemPlanets.filter(pl=>
    ['Barren','Temperate'].includes(pl.type) && limit && pl.radius<=limit);
  const suitable=systemPlanets.filter(pl=>['Barren','Temperate'].includes(pl.type));

  box.innerHTML=`
    ${limit?`<div>${t('fitsUpTo')} <b>${nloc(Math.round(limit))}</b> ${t('km')} (P2/P3)`
      +(limitP4?`, <b>${nloc(Math.round(limitP4))}</b> ${t('km')} (P4)`:'')+`</div>`:''}
    ${systemPlanets.length?`<div class="${fitting.length?'fit':'nofit'}">
      ${t('suitableHere')}: <b>${suitable.length}</b>, ${t('ofThem')}
      <b>${fitting.length}</b> ${fitting.length?'✓':'✗'}</div>`:''}`;
}

async function loadSystemPlanets(){
  const system=document.getElementById('factorySys').value;
  if(!system){ systemPlanets=[]; renderThresholds(); return; }
  try{
    const d=await (await fetch(`${API}/system-planets?system=${encodeURIComponent(system)}`)).json();
    systemPlanets=d.status==='success'?(d.planets||[]):[];
  }catch(e){ systemPlanets=[]; }
  renderThresholds();
}

/* ── Подсказка по вместимости пула ───────────────────────────
   Показывается ДО построения плана: узнавать о нехватке персонажей
   из наполовину построенного плана — значит потратить время впустую.
   Расчёт делает сервер (domain/advice.py), здесь только показ. */
let adviceData=null;

/* Число линий на всю выбранную цепочку (18.09.2026, по прямому запросу
   пользователя — продублировать выбранное, не подбирать второй-третий
   продукт вручную). Одно поле на ВЕСЬ набор target_products, как и
   planner.py::PlanRequest.lines_per_target — не по продукту отдельно. */
function currentLinesPerTarget(){
  return parseInt(document.getElementById('linesPerTarget').value,10)||1;
}

async function loadAdvice(){
  const targets=picked('products');
  const box=document.getElementById('advice');
  if(!targets.length){ box.innerHTML=''; adviceData=null; return; }
  try{
    // purchase_p1 (18.09.2026, найдено пользователем): без него подсказка
    // считала добывающие колонии, которых в этом режиме build_plan() не
    // строит — «персонажей больше, чем нужно» переставала предлагать
    // продукты раньше, чем пул реально заполнялся.
    adviceData=await (await fetch(`${API}/advice`,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({target_products:targets,lang,purchase_p1:purchaseP1,
        lines_per_target:currentLinesPerTarget()})})).json();
  }catch(e){ adviceData=null; }
  renderAdvice();
}

function fmt(template,values){
  return Object.entries(values).reduce((t,[k,v])=>t.replace(`{${k}}`,`<b>${v}</b>`),template);
}

function renderAdvice(){
  const box=document.getElementById('advice'); if(!box) return;
  const a=adviceData;
  if(!a || a.status!=='success' || !['deficit','surplus'].includes(a.verdict)){
    box.innerHTML=''; return;
  }
  const deficit=a.verdict==='deficit';
  const list=deficit?a.alternatives:a.additions;

  const rows=(list||[]).map(s=>`
    <div class="nm">${s.product} <span class="tier ${s.tier}">${s.tier}</span></div>
    <div class="num">${s.colonies} ${t('colonies3')}</div>
    <div class="num isk">${s.isk_per_colony_hour?isk(s.isk_per_colony_hour):'—'}</div>
    <div><button onclick="takeSuggestion('${s.product.replace(/'/g,"\\'")}',${deficit})">
      ${t('advTake')}</button></div>`).join('');

  // Продублировать уже выбранную цепочку целиком (18.09.2026, по прямому
  // запросу пользователя) — приоритетнее подбора другого продукта,
  // поэтому строка идёт ПЕРЕД списком additions/alternatives.
  const dup=(!deficit && a.extra_lines_available>=1)?`
    <div class="sugg-dup">
      <span>${fmt(t('advDuplicateChain'),{n:a.extra_lines_available})}</span>
      <button onclick="duplicateChain(${a.extra_lines_available})">${t('advTake')}</button>
    </div>`:'';

  box.innerHTML=`<div class="advice ${a.verdict}">
    <h4>${deficit?t('advDeficit'):t('advSurplus')} ${helpTag('h-staffing')}</h4>
    <p class="lead">${deficit
      ? fmt(t('advDeficitLead'),{need:a.needed_colonies,have:a.capacity.total_slots})
      : fmt(t('advSurplusLead'),{need:a.needed_colonies,have:a.capacity.total_slots,spare:a.spare_slots})}</p>
    ${dup}
    ${rows?`<div class="sugg">${rows}</div>`:''}
    ${(a.notes||[]).map(n=>`<p class="lead" style="color:var(--dim);margin:8px 0 0">${n}</p>`).join('')}
    ${(deficit || !purchaseP1)?`<div class="foot">
      <button class="mini" onclick="calculate(false, ${!deficit})">
        ${deficit?t('advIgnoreDeficit'):t('advIgnoreSurplus')}</button>
      <span class="hint">${deficit?t('advIgnoreDeficitHint'):t('advIgnoreSurplusHint')}</span>
    </div>`:''}
  </div>`;
}

/* Взять предложенный продукт: при дефиците — вместо выбранного,
   при избытке — вдобавок к нему. */
function takeSuggestion(product, replace){
  if(replace) chosen.products.clear();
  chosen.products.add(product);
  renderProducts(); loadAdvice();
}

/* Продублировать всю выбранную цепочку ещё extraLines раз (18.09.2026,
   по прямому запросу пользователя) — как и takeSuggestion(), только
   готовит настройку («Линий на цепочку»), план строит явная кнопка
   «Построить план». */
function duplicateChain(extraLines){
  const input=document.getElementById('linesPerTarget');
  input.value=(parseInt(input.value,10)||1)+extraLines;
  loadAdvice();
}

/* ── Выбор при упоре в размер планет ──────────────────────────
   Планировщик намеренно НЕ подставляет одиночный шаблон молча:
   это вдвое увеличивает число планет и персонажей, и решать должен
   пользователь. Здесь этот выбор и предлагается. */
function renderDecision(data){
  const box=document.getElementById('decision');
  if(!data || !data.needs_user_decision){ box.innerHTML=''; return; }
  box.innerHTML=`<div class="decision">
    <h4>${t('decisionTitle')}</h4>
    <p>${t('decisionText')}</p>
    ${(data.site_warnings||[]).map(w=>`<p style="color:var(--dim)">${w}</p>`).join('')}
    <div class="acts">
      <button class="mini" onclick="show('setup');document.getElementById('factorySys').focus()">
        ${t('chooseOther')}</button>
      <button class="mini" onclick="calculate(true)">${t('useSingle')}</button>
      <span class="hint">${t('singleNote')}</span>
    </div>
  </div>`;
}

/* ── Сохранённые планы ───────────────────────────────────────
   Хранятся на сервере: план должен переживать перезагрузку и смену
   устройства, а localStorage этого не даёт. */
let savedPlans=[], lastRequest=null, currentPlanId=null;
const cmpPick=new Set();

async function loadSaved(){
  try{ savedPlans=(await (await fetch(`${API}/plans`)).json()).plans||[]; }
  catch(e){ savedPlans=[]; }
  renderSaved();
}

function renderSaved(){
  const box=document.getElementById('savedList'); if(!box) return;
  if(!savedPlans.length){
    box.innerHTML=`<div style="color:var(--dim);font-size:12px">${t('noSaved')}</div>`;
    return;
  }
  box.innerHTML=savedPlans.map(p=>`
    <div class="saved-row${p.id===currentPlanId?' current':''}">
      <input type="checkbox" style="width:auto" ${cmpPick.has(p.id)?'checked':''}
             onchange="toggleCmp('${p.id}',this.checked)">
      <div class="body">
        <div class="nm" title="${esc(p.name)}">${esc(p.name)}</div>
        <div class="meta">${p.colonies} ${t('colonies2')} · ${p.characters} ${t('chars2')} · ${p.peak_load}%</div>
      </div>
      <div class="acts">
        <button class="mini" onclick="openSaved('${p.id}')">${t('load')}</button>
        <button class="mini" onclick="deleteSaved('${p.id}')">${t('del')}</button>
      </div>
    </div>`).join('')
    + (cmpPick.size===2?`<button class="mini" onclick="runCompare()">${t('compare')}</button>`:'');
}

function toggleCmp(id,on){
  on?cmpPick.add(id):cmpPick.delete(id);
  // Больше двух сравнивать нечем: разница определена для пары.
  while(cmpPick.size>2) cmpPick.delete([...cmpPick][0]);
  renderSaved();
}

async function savePlan(){
  if(!plan.length){ alert(t('saveFirst')); return; }
  const name=document.getElementById('planName').value;
  const r=await fetch(`${API}/plans`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name,rows:plan,request:lastRequest||{},
      warnings:[...((lastPlanResp&&lastPlanResp.critical_warnings)||[]),...(lastWarnings||[])],
      assumptions:lastAssumptions||[],
      // Режим purchase_p1 (20.09.2026) — без этого поля список закупки
      // сырья не восстанавливался бы при открытии сохранённого плана
      // (renderPurchaseList() читает его из lastPlanResp.purchased_p1).
      purchased_p1:(lastPlanResp&&lastPlanResp.purchased_p1)||{},
      // Партии-эстафетой + доля продукта при пересечении целей
      // (21.09.2026) — без них повторное открытие плана считало
      // прибыльность заново с нуля (duty_cycle=1.0 везде, revenue_share
      // пустой), теряя все поправки прогноза (найдено пользователем).
      duty_cycles:(lastPlanResp&&lastPlanResp.duty_cycles)||{},
      missing_volumes:(lastPlanResp&&lastPlanResp.missing_volumes)||[],
      revenue_share:(lastPlanResp&&lastPlanResp.revenue_share)||{}})});
  const d=await r.json();
  if(d.status!=='success'){ alert(d.message); return; }
  currentPlanId=d.plan.id;
  document.getElementById('planName').value='';
  loadSaved();
}

async function openSaved(id){
  const d=await (await fetch(`${API}/plans/${id}`)).json();
  if(d.status!=='success'){ alert(d.message); return; }
  plan=d.plan.rows||[]; currentPlanId=id;
  // lastRequest — то же тело, что уходило в /api/calculate при
  // сохранении (domain/plan_storage.py::StoredPlan.request), в нём
  // уже есть target_products/purchase_p1 — без него renderPocoProfit()
  // не вызывает /api/plan-profitability вовсе (её собственная защита
  // от вызова без построенного плана) и прогноз прибыльности молчал
  // при открытии сохранённого плана (найдено пользователем 20.09.2026).
  lastRequest=d.plan.request||null;
  lastWarnings=d.plan.warnings||[]; lastAssumptions=d.plan.assumptions||[];
  // purchased_p1 (величины сырья в час, режим purchase_p1) хранится
  // рядом с планом с 20.09.2026 (infra/models.py::Plan.purchased_p1) —
  // без него renderPurchaseList() не смог бы показать список закупки
  // сырья при повторном открытии плана «переработка без добычи».
  // Планы, сохранённые до этой колонки, честно возвращают {} — список
  // закупки для них не покажется, а прибыльность (выручка/налог) от
  // purchased_p1 не зависит и считается верно в любом случае.
  // duty_cycles/missing_volumes/revenue_share (21.09.2026) — тем же
  // путём: без них renderPocoProfit() шлёт в /api/plan-profitability
  // пустые {}/[], и та честно считает "полную загрузку без поправок"
  // вместо реального прогноза — план "тупел" при каждом повторном
  // открытии (найдено пользователем). Планы до этой колонки — как и с
  // purchased_p1, честно {}/[] (сам прогноз посчитается, просто без
  // поправок причала/пересечения целей, как и было в первой версии
  // фичи).
  lastPlanResp={critical_warnings:[], purchased_p1:d.plan.purchased_p1||{},
    duty_cycles:d.plan.duty_cycles||{}, missing_volumes:d.plan.missing_volumes||[],
    revenue_share:d.plan.revenue_share||{}};
  renderMessages();
  renderAnswers(); renderTable(); renderDash(); renderCrew(); renderShopping(); renderPocoProfit(); renderSaved();
  show('dash');
}

async function deleteSaved(id){
  await fetch(`${API}/plans/${id}`,{method:'DELETE'});
  cmpPick.delete(id);
  if(currentPlanId===id) currentPlanId=null;
  loadSaved();
}

async function runCompare(){
  const [a,b]=[...cmpPick];
  const d=await (await fetch(`${API}/plans/compare?left=${a}&right=${b}`)).json();
  const box=document.getElementById('cmpResult');
  if(d.status!=='success'){ box.innerHTML=`<div class="cmp">${esc(d.message)}</div>`; return; }
  const sign=v=>v>0?`<span class="up">+${v}</span>`:v<0?`<span class="down">${v}</span>`:'0';
  const list=(items,label)=>items.length
    ? `<div style="margin-top:6px">${label}:<ul>${items.map(x=>
        `<li>${esc(x.system)} ${romanNumeral(x.planet)} — ${esc(x.product)}</li>`).join('')}</ul></div>` : '';
  box.innerHTML=`<div class="cmp">
    <div><b>${esc(d.left.name)}</b> → <b>${esc(d.right.name)}</b></div>
    <div style="margin-top:4px">
      ${t('colonies2')}: ${sign(d.delta.colonies)} ·
      ${t('chars2')}: ${sign(d.delta.characters)} ·
      ${t('peak2')}: ${sign(d.delta.peak_load)}%</div>
    <div style="color:var(--dim);margin-top:4px">${t('same')}: ${d.unchanged}</div>
    ${list(d.only_in_left,t('onlyLeft'))}${list(d.only_in_right,t('onlyRight'))}
  </div>`;
}

/* ── Оценка выгоды ────────────────────────────────────────────
   Ранжирование считает сервер (domain/profit.py) по ISK на колонию
   в час. Здесь только показ: если бы браузер считал сам, два места
   могли бы давать разные ответы. */
let profitData=null, lastWarnings=[], lastAssumptions=[], lastLogistics=null, lastPlanResp=null;

async function loadProfit(){
  try{ profitData=await (await fetch(`${API}/market/best-product`)).json(); }
  catch(e){ profitData=null; }
  renderProfit();
}

/* ── Реальные колонии из игры (sync_colony_status) ────────────────
   Это НЕ расчётный план: план — «что стоит построить», здесь — «что
   построено». Обратный отсчёт до конца программы экстрактора считаем
   в браузере из nearest_expiry — сервер не дёргаем каждую минуту. */
let coloniesData=[];

async function loadColonies(){
  try{ coloniesData=(await (await fetch(`${API}/colonies`)).json()).colonies||[]; }
  catch(e){ coloniesData=[]; }
  renderColonies(); renderColoniesProfit();
}

function fmtLeft(ms){
  if(ms<=0) return t('cycOver');
  return fmtDuration(ms);
}

/* Формат плашки-таймера на круге колонии — как в игре: одна единица
   (минуты, пока меньше часа; иначе часы; иначе дни), без склеивания
   «23 ч 18 мин». Плашка маленькая, секунды точности здесь не нужны —
   в отличие от fmtLeft() в панели колонии, где время читают отдельно. */
function fmtBadgeLeft(ms){
  if(ms<=0) return t('cycOver');
  const totalMin=Math.floor(ms/60000);
  if(totalMin<60) return `${totalMin}${t('minUnit')}`;
  const totalH=Math.floor(ms/3600000);
  if(totalH<24) return `${totalH}${t('hUnit')}`;
  return `${Math.floor(ms/86400000)}${t('dUnit')}`;
}

/* Длительность без семантики «осталось»/«кончилось» — для простоя фабрики
   (считает вверх с момента, когда кончился последний цикл) настолько же,
   насколько и для обратного отсчёта. Дни нужны: простой реально бывает
   многодневным (см. «Idle for 7d 6h 15m» у игры), часов без них — абсурд. */
function fmtDuration(ms){
  const d=Math.floor(ms/86400000), h=Math.floor(ms%86400000/3600000), m=Math.floor(ms%3600000/60000);
  if(d>=1) return `${d} ${t('dUnit')} ${h} ${t('hUnit')}`;
  return h>=1 ? `${h} ${t('hUnit')} ${m} ${t('minUnit')}` : `${m} ${t('minUnit')}`;
}

/* Найти реальную колонию под строку расчётного плана: тот же персонаж,
   система и номер планеты. planet_index сборщик кладёт из имени планеты. */
function matchColony(p){
  // p.planet приходит из CSV как «5.0» — сравниваем числа.
  const idx=parseInt(p.planet,10);
  return coloniesData.find(c=>
    c.character===p.character && c.system_name===p.system &&
    c.planet_index===idx) || null;
}

/* Обратный поиск: под реальную колонию — строку расчётного плана
   (если план вообще построен и в нём есть этот персонаж/планета).
   Нужен для кнопки «Детали» — она ведёт в ту же панель по структурам,
   что и колонии плана. */
function matchPlanRow(c){
  return plan.find(p=>p.character===c.character && p.system===c.system_name &&
    parseInt(p.planet,10)===c.planet_index) || null;
}

/* Реальные колонии в форме строки плана (isReal:true) — чтобы дальше
   их вела ровно та же машина рендера: orb(), colonyCardHTML(), режимы
   вида и сортировка. Роль определяем по составу, а не выдумываем:
   есть экстрактор — добыча, нет экстрактора, но есть фабрики —
   переработка, иначе роль неизвестна (только что поставлен CC). */
function realRows(){
  return coloniesData.map(c=>{
    // ESI отдаёт planet_type строчными («ice»), иконки в data/type_ids.json —
    // с заглавной («Ice Command Center»).
    const ptype=c.planet_type?c.planet_type[0].toUpperCase()+c.planet_type.slice(1):'';
    const kinds=(c.structures||[]).map(sd=>sd.kind);
    const role_key=kinds.includes('extractor_control_unit') ? 'mine'
      : kinds.some(k=>k.endsWith('industry_facility')) ? 'proc' : null;
    const match=matchPlanRow(c);
    // «Детали» должна открываться у ЛЮБОЙ реальной колонии, не только у
    // совпавшей со строкой плана: matchId ведёт в панель плана (там есть
    // CPU/PG), синтетический real:char:planet — в openRealColonyDetail().
    // cpu_percent/pg_percent теперь считает сам сервер по НАСТОЯЩИМ данным
    // (структуры+линки+головы из ESI, радиус — из planet_industry.csv, см.
    // scripts/sync_colony_status.py::real_colony_load()) — null, если
    // планеты нет в этом файле (не весь New Eden, только регион плана).
    return {
      isReal:true, matchId:match?match.id:`real:${c.character_id}:${c.planet_id}`,
      character:c.character, system:c.system_name, planet:String(c.planet_index),
      planet_type:ptype, role_key, role_tier:null,
      structures_detail:c.structures||[], pins:c.pins||[], routes:c.routes||[],
      cpu_percent:c.cpu_percent, pg_percent:c.pg_percent,
      cpu_used:c.cpu_used, cpu_capacity:c.cpu_capacity,
      pg_used:c.pg_used, pg_capacity:c.pg_capacity,
      nearest_expiry:c.nearest_expiry, game_last_update:c.game_last_update,
      upgrade_level:c.upgrade_level, num_pins:c.num_pins,
    };
  });
}

/* Та же сортировка, что и у плана (sorted()), на тех же кнопках —
   только «загрузка»/«запас» не про CPU/PG (её нет), а про срочность
   обратного отсчёта: самая горящая программа первой / самая свободная
   первой. Персонаж и система сравниваются буквально как у плана. */
function sortedColonies(){
  const rows=realRows();
  const left=c=>c.nearest_expiry?Date.parse(c.nearest_expiry)-Date.now():Infinity;
  if(sortBy==='load')    rows.sort((a,b)=>left(a)-left(b));
  if(sortBy==='reserve') rows.sort((a,b)=>left(b)-left(a));
  if(sortBy==='pilot')   rows.sort((a,b)=>a.character.localeCompare(b.character)||a.system.localeCompare(b.system));
  if(sortBy==='system')  rows.sort((a,b)=>a.system.localeCompare(b.system)||(+a.planet)-(+b.planet));
  return rows;
}

/* «Мои колонии в игре» — те же три режима вида (детали/список/сетка) и
   та же сортировка, что и у плана дашборда: одни кнопки управляют обоими
   блоками, поведение не должно расходиться. */
/* Быстрая навигация — план (dashBody) обычно длиннее экрана, а «Мои
   колонии» и «Список закупки» идут ниже него; .tools прилипает к верху
   (position:sticky), поэтому кнопки прыжка там всегда под рукой, даже
   когда план прокручен на много колоний вниз. Видимость переключается
   в renderColonies()/renderShopping() — кнопка есть, только пока в
   соответствующем блоке действительно есть что показать.
   «↓ Мои колонии» целится в #coloniesProfit, не в #colonies (решение
   пользователя, 16.09.2026): панель прибыльности по факту стоит прямо
   над списком колоний, так кнопка сразу показывает и её; когда панели
   нечего показать (нет ни одной колонии с известной ролью), пустой
   #coloniesProfit не занимает места, и прокрутка визуально совпадает
   со списком колоний, как раньше.
   Той же симметрии ради «↑ План» целится в #pocoProfit, не в #dashBody:
   «Прогноз прибыльности плана» (перенесён на дашборд с «Настроек»,
   16.09.2026) стоит прямо НАД карточками плана, а не под ними — цель
   для прыжка должна быть верхом всего блока «план», иначе прыжок вверх
   молча пропускал бы панель прибыльности. Пустой #pocoProfit (план ещё
   не построен) так же не занимает места. */
function jumpTo(id){
  document.getElementById(id).scrollIntoView({behavior:'smooth', block:'start'});
}

function renderColonies(){
  const box=document.getElementById('colonies'); if(!box) return;
  const jumpBtn=document.getElementById('jumpColonies');
  if(!coloniesData.length){ box.innerHTML=''; if(jumpBtn) jumpBtn.style.display='none'; return; }
  if(jumpBtn) jumpBtn.style.display='';
  const rows=sortedColonies();
  let body='';
  if(view==='details'){
    body=rows.map(colonyCardHTML).join('');
  } else if(view==='list'){
    const byPilot={};
    rows.forEach(p=>(byPilot[p.character]=byPilot[p.character]||[]).push(p));
    // Свободные слоты — planet_slots персонажа (Interplanetary Consolidation
    // + 1, /api/characters, та же формула, что и в самом расчёте) минус
    // реально построенные колонии. Только там, где planet_slots вообще
    // известны — иначе честно ни одного пунктирного кружка не рисуем.
    body=Object.entries(byPilot).map(([name,items])=>{
      const c=crew.find(x=>x.name===name);
      const freeN=c?Math.max(0,c.planet_slots-items.length):0;
      const freeSlots=Array.from({length:freeN},()=>`<div class="orb-slot" title="${t('freeSlot')}">+</div>`).join('');
      return `
      <div class="pilot-row">
        <div class="who" title="${esc(name)} · ${pl(items.length,'pColony')}">
          <div class="pilot-lg" style="width:52px;height:52px;font-size:16px">${initials(name)}${avatarImg(charIdOf(name))}</div>
        </div>
        <div class="orbs">${items.map(p=>orb(p,52,false)).join('')}${freeSlots}</div>
      </div>`;
    }).join('');
  } else if(view==='grid'){
    body=`<div class="grid">${rows.map(p=>orb(p,58)).join('')}</div>`;
  }
  const idleN=rows.filter(isColonyIdle).length;
  // «Требует внимания» — переопределено 16.09.2026 (docs/ROADMAP.md,
  // Фаза 9, решение пользователя): только «причал полон», без
  // заблаговременного 2-часового предупреждения об истечении программы
  // экстрактора — «только когда уже встала» (та ситуация — категория
  // idleN, не эта; счётчики независимые, колония может попасть в ОБА
  // сразу, не одно ИЛИ другое). Причал полон — сигнал ТОЛЬКО для добычи:
  // складывать больше некуда — плохо; у переработки полный причал —
  // запас сырья на будущее, скорее хорошо, не «требует внимания» (сама
  // проверка `isLaunchpadFull()` не трогается — тот же процент нужен и
  // для честного кольца заполненности независимо от роли). Дефицит
  // добычи — своя категория рядом, не смешана сюда: колония в дефиците
  // всё ещё работает (просто ниже DEFICIT_UNITS_PER_HOUR), это другая по
  // смыслу проблема, и у неё свой маркер (.def-mark), не «запрет».
  const attnN=rows.filter(p=>p.role_key==='mine'
    &&isLaunchpadFull(p, simulateColonyFactories(p.pins,p.routes,p.game_last_update))).length;
  const deficitN=rows.filter(isColonyDeficient).length;
  const summary=`${pl(rows.length,'pPlanetN')} · ${idleN} ${t('coloniesIdle')} · ${attnN} ${t('coloniesAttention')}`
    + ` · ${deficitN} ${t('coloniesDeficit')}`;
  box.innerHTML=`<div class="colonies"><h3>${t('inGameColonies')} ${helpTag('h-ingame')} <span class="colsum">${summary}</span></h3>${body}</div>`;
  fixTipOverflowIn(box);
}

/* Живой отсчёт: перерисовываем только числа, не весь блок. Работает
   для .tail .val (soon/over), .extractor-left в панели колонии и
   .orb .badge в list/grid-видах «Моих колоний» (hot) — у каждого свой
   класс подсветки, поэтому тут выставляются оба набора сразу. */
setInterval(()=>{
  const now=Date.now();
  document.querySelectorAll('[data-exp]').forEach(el=>{
    const left=+el.dataset.exp-now;
    // Плашка на круге колонии (.orb .badge) — короткий формат в одну
    // единицу (см. fmtBadgeLeft); везде ещё, где есть data-exp (панель
    // колонии, .tail .val) — обычный многоединичный fmtLeft(). Раньше
    // тут везде стоял один fmtLeft() — плашка правильно рисовалась при
    // первом renderColonies(), но на следующем тике таймера (раз в
    // минуту) сбивалась обратно на «21 ч 59 мин».
    el.textContent=el.classList.contains('badge')?fmtBadgeLeft(left):fmtLeft(left);
    el.classList.toggle('soon', left>0 && left<7200000);
    el.classList.toggle('over', left<=0);
    el.classList.toggle('hot', left<=0);
  });
  // Прогресс-бар и «простаивает N» у фабрик (см. structureUnitFields)
  // считаются по last_cycle_start на каждой перерисовке панели — проще
  // и надёжнее точечных DOM-патчей: юнитов в открытой колонии немного.
  if(currentColony && document.getElementById('colonyPage').classList.contains('on'))
    openColony(currentColony);
}, 60000);

function isk(v){
  return v==null ? '—' : Math.round(v).toLocaleString(lang==='ru'?'ru':'en-US');
}

function renderProfit(){
  const box=document.getElementById('profit'); if(!box) return;
  const snap=profitData && profitData.snapshot;
  if(!snap || !snap.has_data){
    box.innerHTML=`<div class="profit"><div class="profit-head"><h3>${t('profitTitle')} ${helpTag('h-profit-ranking')}</h3></div>
      <div style="color:var(--dim);font-size:12px">${t('noSnapshot')}. ${t('noSnapshotHint')}</div></div>`;
    return;
  }
  const rows=(profitData.analytics||[]).slice(0,12);
  box.innerHTML=`<div class="profit">
    <div class="profit-head"><h3>${t('profitTitle')} ${helpTag('h-profit-ranking')}</h3>
      <span class="age${snap.stale?' stale':''}">${t('collectedAgo')} ${snap.age_minutes} ${t('minutesAgo')}`
      +(snap.stale?` · ${t('stale')}`:'')+` · ${snap.station||''}</span></div>
    <table><thead><tr class="cols">
      <th>#</th><th>${t('colProduct')}</th><th class="r">${t('colPrice')}</th>
      <th class="r">${t('colColonies')}</th><th class="r">${t('colPerHour')}</th>
      <th class="r">${t('colPerColony')}</th></tr></thead>
    <tbody>${rows.map((r,i)=>`
      <tr class="${i===0&&r.isk_per_colony_hour?'best':''}${r.isk_per_colony_hour?'':' noprice'}">
        <td class="idx num">${i+1}</td>
        <td class="name">${r.product} <span class="tier ${r.tier}">${r.tier}</span></td>
        <td class="r num">${r.price_per_unit?isk(r.price_per_unit):t('noPrice')}</td>
        <td class="r num">${r.total_colonies}</td>
        <td class="r num">${isk(r.revenue_per_hour)}</td>
        <td class="r num" style="color:${r.isk_per_colony_hour?'var(--bright)':'inherit'}">${isk(r.isk_per_colony_hour)}</td>
      </tr>`).join('')}</tbody></table>
    <div class="profit-note">${t('profitNote')}</div></div>`;
}

/* ── Прибыльность плана с учётом POCO (docs/ROADMAP.md, Фаза 9,
   16.09.2026; ручной ввод ставки по типу убран 17.09.2026 при переходе
   к мультирегиональности) ───────────────────────────────────────────
   Ставка — точная автоматическая ставка КОНКРЕТНОЙ планеты из
   data/planet_industry.csv (domain/planets.py::PlanetBook.poco_rate()).
   Планета без известной ставки — domain/poco_tax.py честно исключает
   её из расчёта (missing_rates), не подставляет «без налога». */
async function renderPocoProfit(){
  const box=document.getElementById('pocoProfit'); if(!box) return;
  if(!plan.length || !lastRequest){ box.innerHTML=''; lastPurchaseData=null; return; }

  let d;
  try{
    const r=await fetch(`${API}/plan-profitability`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        rows:plan, target_products:lastRequest.target_products,
        purchased_p1:(lastPlanResp&&lastPlanResp.purchased_p1)||{},
        duty_cycles:(lastPlanResp&&lastPlanResp.duty_cycles)||{},
        missing_volumes:(lastPlanResp&&lastPlanResp.missing_volumes)||[],
        revenue_share:(lastPlanResp&&lastPlanResp.revenue_share)||{},
      }),
    });
    d=await r.json();
  }catch(e){ box.innerHTML=''; lastPurchaseData=null; return; }
  if(!d || d.status!=='success'){ box.innerHTML=''; lastPurchaseData=null; return; }

  const gaps=[];
  if(d.missing_prices && d.missing_prices.length)
    gaps.push(fmtPlain(t('pocoMissingPrices'),{list:d.missing_prices.join(', ')}));
  if(d.missing_rates && d.missing_rates.length)
    gaps.push(fmtPlain(t('pocoMissingRates'),{list:d.missing_rates.join(', ')}));
  if(d.missing_volumes && d.missing_volumes.length)
    gaps.push(fmtPlain(t('pocoMissingVolumes'),{list:d.missing_volumes.join(', ')}));
  // Пересечение целевых цепочек (20.09.2026) уже учтено в самой выручке
  // (domain/poco_tax.py — revenue_share, doc/ROADMAP.md) — отдельная
  // заметка про доли убрана из панели по прямому запросу пользователя
  // как избыточная информация; расчёт при этом не меняется.

  // Пропускная способность причала (20.09.2026, по прямому запросу
  // пользователя) — причал фиксированной ёмкости опустошается тем
  // быстрее, чем больше видов и тяжелее сырьё тира; ниже 0.99 считаем
  // достаточно заметным, чтобы показать честно, а не тонуть в шуме
  // округления около единицы.
  const bottlenecks=Object.entries((lastPlanResp&&lastPlanResp.duty_cycles)||{})
    .filter(([,cycle])=>cycle<0.99)
    .sort((a,b)=>a[1]-b[1]);

  const netKnown=d.monthly_net_profit!=null;
  const notes=[
    ...gaps,
    (!netKnown&&(d.monthly_revenue!=null||d.monthly_tax!=null))?t('pocoIncomplete'):'',
    bottlenecks.length?`${t('pocoLogisticsBottleneck')} ${
      bottlenecks.map(([product,cycle])=>`${product} — ${Math.round(cycle*100)}%`).join(', ')
    }`:'',
    fmtPlain(t('pocoAssumption'),{hours:d.hours_per_month}),
  ].filter(Boolean);

  box.innerHTML=`<div class="poco-profit">
    <div class="profit-head"><h3>${t('pocoProfitTitle')} ${helpTag('h-profitability')}</h3></div>
    <div class="figures">
      <div class="figure"><div class="lbl">${t('pocoRevenue')}</div>
        <div class="val${d.monthly_revenue==null?' dim':''}">${d.monthly_revenue==null?t('noData'):isk(d.monthly_revenue)}</div></div>
      <div class="figure"><div class="lbl">${t('pocoTax')}</div>
        <div class="val${d.monthly_tax==null?' dim':''}">${d.monthly_tax==null?t('noData'):isk(d.monthly_tax)}</div></div>
      ${d.monthly_purchase_cost!=null?`<div class="figure"><div class="lbl">${t('pocoPurchaseCost')}</div>
        <div class="val">${isk(d.monthly_purchase_cost)}</div></div>`:''}
      <div class="figure net"><div class="lbl">${t('pocoNetProfit')}</div>
        <div class="val${netKnown?'':' dim'}">${netKnown?isk(d.monthly_net_profit):t('noData')}</div></div>
    </div>
    <div class="poco-row">
      <div class="step-card hud-corner">
        <div class="step-head"><span class="step-title">${t('pocoRevenueByProductTitle')}</span></div>
        <div class="step-body">
          ${(d.revenue_by_product&&d.revenue_by_product.length)?`<table class="purchase-list-table">
            <thead><tr><th>${t('purchaseListProduct')}</th><th class="r">${t('pocoRevenueByProductQty')}</th>
              <th class="r">${t('pocoRevenueByProductPrice')}</th><th class="r">${t('pocoRevenueByProductRevenue')}</th></tr></thead>
            <tbody>${d.revenue_by_product.map(it=>`<tr>
              <td>${it.product}</td>
              <td class="r num">${nloc(Math.round(it.monthly_units))}</td>
              <td class="r num">${it.price==null?t('purchaseListNoPrice'):nloc(Math.round(it.price))}</td>
              <td class="r num">${it.monthly_revenue==null?'—':isk(it.monthly_revenue)}</td>
            </tr>`).join('')}</tbody>
          </table>`:`<div class="poco-profit-note">${t('noData')}</div>`}
        </div>
      </div>
      ${(d.purchase_items&&d.purchase_items.length)?'<div id="purchaseList"></div>':''}
      <div class="step-card hud-corner">
        <div class="step-head"><span class="step-title">${t('pocoNotesTitle')}</span></div>
        <div class="step-body">
          ${notes.map(n=>`<div class="poco-profit-note">${n}</div>`).join('')}
        </div>
      </div>
    </div>
  </div>`;
  renderPurchaseList(d);
}

/* ── Список закупки сырья P1 (19.09.2026, по прямому запросу
   пользователя: «в список покупок должно быть добавлено сырьё на
   месяц с кол-вом по виду ресурса и ценой на момент построения
   плана») ────────────────────────────────────────────────────────
   Данные — из того же ответа /api/plan-profitability, что и панель
   прибыльности (purchase_items/prices_collected_at,
   domain/poco_tax.py::PurchaseItem) — не отдельный запрос: план уже
   считался единожды, повторный вызов дал бы другой снимок цен и
   разошёлся бы с «Закупка P1 / мес» в соседней панели. Пусто —
   когда план не в режиме purchase_p1 или закупать нечего (лишний
   пустой блок хуже отсутствия блока). Результат кладётся в
   lastPurchaseData — тем же снимком цен пользуется exportPlan(), чтобы
   в Excel попали ИМЕННО те цены, что видел пользователь на экране, а
   не более свежие с сервера на момент нажатия «Экспорт». */
let lastPurchaseData=null;
function renderPurchaseList(d){
  const box=document.getElementById('purchaseList'); if(!box) return;
  const items=(d&&d.purchase_items)||[];
  if(!items.length){ box.innerHTML=''; lastPurchaseData=null; return; }
  lastPurchaseData={items, prices_collected_at:d.prices_collected_at||null};

  let stamp='';
  if(d.prices_collected_at){
    try{ stamp=': '+new Date(d.prices_collected_at).toLocaleString(lang==='ru'?'ru':'en-US'); }
    catch(e){ stamp=''; }
  }
  const totalCost=items.reduce((sum,it)=>sum+(it.monthly_cost||0),0);
  const anyKnown=items.some(it=>it.monthly_cost!=null);

  box.innerHTML=`<div class="step-card hud-corner">
    <div class="step-head"><span class="step-title">${t('purchaseListTitle')}</span> ${helpTag('h-purchase-list')}</div>
    <div class="step-body">
      <table class="purchase-list-table"><thead><tr>
        <th>${t('purchaseListProduct')}</th><th class="r">${t('purchaseListQty')}</th>
        <th class="r">${t('purchaseListPrice')}</th><th class="r">${t('purchaseListCost')}</th>
      </tr></thead><tbody>
      ${items.map(it=>`<tr>
        <td>${it.product}</td>
        <td class="r num">${nloc(Math.round(it.monthly_qty))}</td>
        <td class="r num">${it.price==null?t('purchaseListNoPrice'):nloc(Math.round(it.price))}</td>
        <td class="r num">${it.monthly_cost==null?'—':isk(it.monthly_cost)}</td>
      </tr>`).join('')}
      ${anyKnown?`<tr class="purchase-list-total"><td colspan="3">${t('purchaseListTotal')}</td>
        <td class="r num">${isk(totalCost)}</td></tr>`:''}
      </tbody></table>
      <div class="poco-profit-note">${fmtPlain(t('purchaseListAsOf'),{stamp:stamp||t('purchaseListNoSnapshot')})}</div>
    </div>
  </div>`;
}

/* ── Прибыльность НАСТОЯЩИХ колоний с учётом POCO (docs/ROADMAP.md,
   Фаза 9, 16.09.2026, пункт после версии для плана) ─────────────────
   В отличие от плана: скорость каждой колонии — реальная текущая
   (colonyOutputRatePerHour, с поправкой на затухание/простой), не
   теоретический максимум; налог — только экспортный, поколонийно, без
   графа между колониями (ESI не отдаёт, куда игрок физически везёт
   сырьё МЕЖДУ разными колониями). Ставки POCO — тот же ввод, что и у
   плана, одна настройка на обе функции. Показывается агрегированной
   строкой над списком «Мои колонии в игре» (решение пользователя), не
   на карточке каждой колонии по отдельности. */
async function renderColoniesProfit(){
  const box=document.getElementById('coloniesProfit'); if(!box) return;
  const rows=realRows().filter(r=>r.role_key);
  if(!rows.length){ box.innerHTML=''; return; }

  const sims=new Map(rows.map(r=>[r, r.pins&&r.pins.length?simulateColonyFactories(r.pins,r.routes,r.game_last_update):null]));
  const colonies=rows.map(r=>{
    const flow=colonyOutputFlow(r);
    return {
      label:`${r.character} · ${r.system} ${romanNumeral(r.planet)}`,
      product: flow?flow.pin.product:null,
      units_per_hour: flow?colonyOutputRatePerHour(r, sims.get(r)):null,
      planet_type: r.planet_type,
      // Для точной автоматической ставки POCO этой планеты из
      // data/planet_industry.csv (domain/poco_tax.py, 17.09.2026) —
      // planet_type один на весь сервер не даёт этого сделать точно.
      system: r.system, planet: r.planet,
    };
  });

  let d;
  try{
    const r=await fetch(`${API}/colonies-profitability`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({colonies}),
    });
    d=await r.json();
  }catch(e){ box.innerHTML=''; return; }
  if(!d || d.status!=='success'){ box.innerHTML=''; return; }

  const gaps=[];
  if(d.missing_output && d.missing_output.length)
    gaps.push(fmtPlain(t('coloniesProfitMissingOutput'),{list:d.missing_output.join(', ')}));
  if(d.missing_prices && d.missing_prices.length)
    gaps.push(fmtPlain(t('pocoMissingPrices'),{list:d.missing_prices.join(', ')}));
  if(d.missing_rates && d.missing_rates.length)
    gaps.push(fmtPlain(t('pocoMissingRates'),{list:d.missing_rates.join(', ')}));

  const netKnown=d.monthly_net_profit!=null;
  box.innerHTML=`<div class="poco-profit">
    <div class="profit-head"><h3>${t('coloniesProfitTitle')} ${helpTag('h-colonies-profit')}</h3></div>
    <div class="figures">
      <div class="figure"><div class="lbl">${t('pocoRevenue')}</div>
        <div class="val${d.monthly_revenue==null?' dim':''}">${d.monthly_revenue==null?t('noData'):isk(d.monthly_revenue)}</div></div>
      <div class="figure"><div class="lbl">${t('pocoTax')}</div>
        <div class="val${d.monthly_tax==null?' dim':''}">${d.monthly_tax==null?t('noData'):isk(d.monthly_tax)}</div></div>
      <div class="figure net"><div class="lbl">${t('pocoNetProfit')}</div>
        <div class="val${netKnown?'':' dim'}">${netKnown?isk(d.monthly_net_profit):t('noData')}</div></div>
    </div>
    ${gaps.length?`<div class="poco-profit-note">${gaps.join(' · ')}</div>`:''}
    ${!netKnown&&(d.monthly_revenue!=null||d.monthly_tax!=null)?`<div class="poco-profit-note">${t('pocoIncomplete')}</div>`:''}
    <div class="poco-profit-note">${t('coloniesProfitAssumption')}</div>
  </div>`;
}

/* ── Список закупки ──────────────────────────────────────────
   Считается по типам планет: командный центр покупается под тип,
   и в игре это отдельный предмет с собственным type_id. */
function renderShopping(){
  const box=document.getElementById('shopping');
  const jumpBtn=document.getElementById('jumpShopping');
  if(!plan.length){ box.innerHTML=''; if(jumpBtn) jumpBtn.style.display='none'; return; }
  if(jumpBtn) jumpBtn.style.display='';
  const byType={};
  plan.forEach(p=>{ byType[p.planet_type]=(byType[p.planet_type]||0)+1; });
  const total=Object.values(byType).reduce((a,b)=>a+b,0);
  box.innerHTML=`<div class="shop">
    <h3>${t('shopping')}</h3>
    <div class="shop-grid">${Object.entries(byType).sort().map(([type,n])=>{
      const id=ids[`${type} Command Center`];
      return `<div class="shop-item hud-corner">
        ${id?`<img src="${ICON(id)}" alt="">`:'<span style="width:30px"></span>'}
        <span class="n">${type}</span><span class="q">${n}</span></div>`;
    }).join('')}</div>
    <div class="shop-total">${t('shopTotal')}: ${total}</div>
  </div>`;
}

/* ── Выгрузка в Excel ────────────────────────────────────────
   Файл собирает сервер (openpyxl), браузер только скачивает:
   так в книге остаются формулы, которые пересчитываются при правке. */
/* 18.09.2026, по прямому запросу пользователя: экспорт теперь берёт и
   план, и настоящие колонии, если они есть, — а не только план. Кнопка
   недоступна, только если нет вообще ничего: ни расчётного плана, ни
   хоть одной синхронизированной колонии. Сама сборка листов — на
   сервере (api/blueprints/export.py::_build_workbook()), тело запроса
   просто передаёт то, что уже загружено на дашборде (plan/coloniesData),
   без пересчёта на фронтенде. */
/* coloniesData целиком — САМ пин несёт extraction_history (копится с
   13.09.2026, ничем не ограничена — на реальном счёте пользователя уже
   ~21 000 записей на 87 колоний, найдено 18.09.2026 при разборе HTTP 413
   от nginx) и contents/routes — ничего из этого _colony_to_row() на
   сервере не читает (только character/system_name/planet_index/
   planet_type/cpu_percent/pg_percent и per-pin kind/product/heads).
   Отправлять это в экспорт — раздувать тело запроса без всякой пользы, и
   рано или поздно упереться в лимит nginx снова, сколько его ни поднимай.
   Поэтому для экспорта — свой, урезанный список полей, а не coloniesData
   как есть. heads — не лишний: без него число голов экстрактора терялось
   по дороге и «Структуры» у чисто добывающих колоний показывали «1
   фабрик» вместо реального числа (18.09.2026, тот же реальный отчёт, что
   и фикс роли/входа/выхода). */
function coloniesForExport(){
  return coloniesData.map(c=>({
    character:c.character, system_name:c.system_name, planet_index:c.planet_index,
    planet_type:c.planet_type, cpu_percent:c.cpu_percent, pg_percent:c.pg_percent,
    pins:(c.pins||[]).map(p=>({kind:p.kind, product:p.product, heads:p.heads})),
  }));
}

async function exportPlan(){
  if(!plan.length && !coloniesData.length){ alert(t('exportEmpty')); return; }
  const btn=document.getElementById('exportBtn');
  btn.disabled=true;
  try{
    const r=await fetch(`${API}/export`,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        plan_data:plan,colonies_data:coloniesForExport(),lang,
        // Тот же снимок, что уже показан на панели «Список закупки
        // сырья» (lastPurchaseData, renderPurchaseList()) — не
        // пересчитывается заново на сервере: свежий снимок цен на
        // момент нажатия «Экспорт» разошёлся бы с тем, что видел
        // пользователь на экране секундами раньше.
        purchase_items:(lastPurchaseData&&lastPurchaseData.items)||[],
        prices_collected_at:(lastPurchaseData&&lastPurchaseData.prices_collected_at)||null,
      })});
    if(!r.ok){
      const d=await r.json().catch(()=>({}));
      throw new Error(d.message||`HTTP ${r.status}`);
    }
    const blob=await r.blob();
    const url=URL.createObjectURL(blob), a=document.createElement('a');
    a.href=url;
    a.download=(r.headers.get('Content-Disposition')||'').match(/filename=([^;]+)/)?.[1]
      || `pi-plan.xlsx`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }catch(e){ alert(`${t('exportFail')}: ${e.message}`); }
  finally{ btn.disabled=false; }
}

/* ── Панель колонии: разбор по структурам, как в игре ─────────── */
let currentColony=null;

/* Содержимое склада/причала/буфера фабрики — настоящие contents ESI
   (амаунт + type_id, имя резолвит sync_colony_status.py). Пустой список —
   это тоже ответ («на складе пусто»), отдельно от «нет данных». */
function contentsHTML(list){
  if(!list) return `<div class="meta">${t('noData')}</div>`;
  if(!list.length) return `<div class="meta">${t('emptyStorage')}</div>`;
  return list.map(c=>`<div class="meta">
    ${ids[c.name]?`<img src="${ICON(ids[c.name])}" alt="" style="width:14px;height:14px;vertical-align:-3px;margin-right:3px">`:''}
    ${nloc(Math.round(c.amount))} × ${c.name||('#'+c.type_id)}</div>`).join('');
}

/* Поля правой панели одной структуры.
   ctx.pin — настоящие поштучные данные ESI этого пина (см.
   scripts/sync_colony_status.py::pin_detail) — есть только у факта из
   игры, когда колония пересинхронизирована новым кодом. Без него (план,
   ещё не пересинхронизированный факт) — прежнее поведение.

   ctx.hasLoad=false — у факта из игры без совпавшего плана нет CPU/PG
   (ESI не отдаёт радиус планеты, без него линки не посчитать — оценка
   без них была бы «правдоподобным, но ложным» числом, правило 1). */
/* Причал/склад/собственное хранилище командного центра — тот же вид:
   used_m3/capacity_m3 (реальный объём содержимого против игровой
   константы ёмкости, оба с сервера, см. scripts/sync_colony_status.py
   ::pin_detail) и список содержимого. used_m3 бывает null, если пины
   ещё не пересинхронизированы новым кодом или объём хоть одного
   предмета не узнать у ESI — тогда честно «нет данных», а не 0%.

   projectedContents/projectedUsedM3 (необязательно) — то же самое, но
   досчитанное вперёд до «сейчас» simulateColonyFactories() (см. её
   snapshotResults): расход сырья фабриками и накопление готовой
   продукции по маршрутам, а не статичный снимок ESI с последней
   синхронизации (найдено пользователем 17.09.2026: список не менялся
   между синхронизациями, готовый продукт не показывался вовсе, а
   заполненность в м³ стояла на месте, хотя состав со временем сдвигается
   от «тяжёлого» сырья к более «лёгкой» готовой продукции и реальный
   занятый объём заметно падает). Объём на единицу товара теперь тоже
   известен фронту (typeVolumes, /api/initial-data — тот же кэш, которым
   сервер считает used_m3 из настоящего снимка, type_volume()), поэтому
   считать можно и на фронтенде; честный null в projectedUsedM3, если
   объём хоть одного предмета в проекции неизвестен — используется тогда
   статичный pin.used_m3 из последнего снимка, как и раньше. */
function storageBlockHTML(pin, projectedContents, projectedUsedM3){
  const usedM3=projectedUsedM3!=null?projectedUsedM3:(pin?pin.used_m3:null);
  const known=usedM3!=null&&pin&&pin.capacity_m3!=null;
  const pct=known?Math.min(100,100*usedM3/pin.capacity_m3):0;
  const contents=projectedContents!=null?projectedContents:(pin?pin.contents:null);
  return `<div class="cols2">
    <div><div class="lbl">${t('capCap')}${known?`: ${pct.toFixed(0)}%`:''}</div>
      <div class="meter"><i class="ok" style="width:${pct}%;background:var(--ring-infra)"></i></div>
      <div class="val">${known?`${nloc(Math.round(usedM3))} / ${nloc(pin.capacity_m3)} m³`:t('noData')}</div></div>
    <div><div class="lbl">${t('capContent')}</div>${contentsHTML(contents)}</div>
  </div>`;
}

function structureUnitFields(kind, ctx){
  const isStore=kind==='launchpad'||kind==='storage_facility';
  const isECU=kind==='extractor_control_unit';
  const isFactory=kind.includes('industry_facility');
  const pin=ctx.pin;

  if(kind==='command_center'){
    // Собственное хранилище командного центра (500 м³ — верифицировано
    // скриншотом игрового клиента, ESI ёмкость структур не отдаёт) —
    // независимо от того, посчиталась ли загрузка CPU/Power: contents
    // приходит с тем же пином, что и всегда.
    const storage=`<div style="margin-top:10px">${storageBlockHTML(pin, ctx.sim&&ctx.sim.contents, ctx.sim&&ctx.sim.usedM3)}</div>`;
    if(!ctx.hasLoad){
      return `<div class="cols2">
        <div><div class="lbl">CPU</div>
          <div class="meter"><i class="cpu" style="width:0%"></i></div>
          <div class="meta">${t('noData')}</div></div>
        <div><div class="lbl">Power</div>
          <div class="meter"><i class="pg" style="width:0%"></i></div>
          <div class="meta">${t('noData')}</div></div>
      </div>${storage}`;
    }
    // pgb — разбивка «из чего сложилась загрузка» (структуры/головы/линки
    // в процентах) есть только у расчётного плана (planner.py считает её
    // заодно с самим процентом). У настоящей колонии свой источник радиуса
    // (planet_industry.csv, а не шаблон) и разбивки не считает — сама
    // загрузка от этого не менее настоящая, просто без деталей «из чего».
    const pgb=ctx.pgb;
    // Абсолютные числа (tf/MW) рядом с процентом — как в игровом клиенте
    // (скриншот пользователя 11.09.2026); есть только у настоящей колонии
    // (ctx.cpuUsed и т.п.), план отдаёт только проценты.
    const cpuAbs=ctx.cpuUsed!=null?`<div class="val">${nloc(Math.round(ctx.cpuUsed))}/${nloc(Math.round(ctx.cpuCap))} tf</div>`:'';
    const pgAbs=ctx.pgUsed!=null?`<div class="val">${nloc(Math.round(ctx.pgUsed))}/${nloc(Math.round(ctx.pgCap))} MW</div>`:'';
    return `<div class="cols2">
      <div><div class="lbl">CPU: ${ctx.cpu.toFixed(1)}%</div>
        <div class="meter"><i class="cpu" style="width:${Math.min(ctx.cpu,100)}%"></i></div>${cpuAbs}</div>
      <div><div class="lbl">Power: ${ctx.pg.toFixed(1)}%</div>
        <div class="meter"><i class="pg${ctx.pg<90?' ok':''}" style="width:${Math.min(ctx.pg,100)}%"></i></div>${pgAbs}</div>
    </div>
    ${pgb?`<div class="meta" style="margin-top:6px">
      ${t('pgFrom')}: ${t('sStruct')} ${pgb.structures||0}% · ${t('sHeads')} ${pgb.heads||0}% · ${t('sLinks')} ${pgb.links||0}%</div>`:''}${storage}`;
  }

  if(isStore){
    return storageBlockHTML(pin, ctx.sim&&ctx.sim.contents, ctx.sim&&ctx.sim.usedM3);
  }

  if(isECU){
    // pin — реальные данные конкретного экстрактора (heads/product из
    // extractor_details, раньше не читались вовсе).
    if(pin){
      if(!pin.expiry_time){
        return `<div class="cols2">
          <div><div class="lbl">${t('cycNow')}</div>
            <div class="meter"><i style="width:0%;background:var(--ring-extract)"></i></div>
            <div class="meta" style="color:var(--alarm)">${t('cycIdle')}</div></div>
          <div><div class="lbl">${t('extractsW')}</div>
            <div class="val">${pin.product||'—'}</div>
            <div class="meta">${pin.heads?pl(pin.heads,'pHead'):t('heads10')}</div></div>
        </div>${extractorRateHTML(pin)}`;
      }
      const expMs=Date.parse(pin.expiry_time);
      return `<div class="cols2">
        <div><div class="lbl">${t('cycNow')}</div>
          <div class="meter"><i style="width:0%;background:var(--ring-extract)"></i></div>
          <div class="val extractor-left" data-exp="${expMs}">${fmtLeft(expMs-Date.now())}</div>
          <div class="meta">${t('progEnds')}</div></div>
        <div><div class="lbl">${t('extractsW')}</div>
          <div class="val">${ids[pin.product]?`<img src="${ICON(ids[pin.product])}" alt="" style="width:14px;height:14px;vertical-align:-3px;margin-right:3px">`:''}${pin.product||'—'}</div>
          <div class="meta">${pin.heads?pl(pin.heads,'pHead'):t('heads10')}</div></div>
      </div>${extractorRateHTML(pin)}`;
    }
    const cyc = ctx.expMs!=null
      ? `<div class="val extractor-left" data-exp="${ctx.expMs}">${fmtLeft(ctx.expMs-Date.now())}</div>
         <div class="meta">${t('progEnds')}</div>`
      : ctx.real
        ? `<div class="meta">${t('cycIdle')}</div>`
        : `<div class="meta">${t('cycNone')}</div>`;
    return `<div class="cols2">
      <div><div class="lbl">${t('cycNow')}</div>
        <div class="meter"><i style="width:0%;background:var(--ring-extract)"></i></div>
        ${cyc}</div>
      <div><div class="lbl">${t('extractsW')}</div>
        <div class="val">${ctx.resIn||'—'}</div>
        <div class="meta">${t('heads10')}</div></div>
    </div>`;
  }

  if(isFactory){
    // pin.last_cycle_start + pin.cycle_minutes (продукт и длительность —
    // с самой ESI, GET /universe/schematics/{id}/, см. schematic_info()
    // в sync_colony_status.py). Прогресс/простой — pinCycleInfo(), без
    // переноса по модулю (см. её докстринг: почему модуль был неверен —
    // скриншот из стороннего PI-инструмента с разным простоем у разных
    // фабрик).
    if(pin){
      const info=pinCycleInfo(pin,ctx.gameLastUpdate,ctx.sim);
      let state;
      if(info.unknown){
        state=`<div class="state-wait">${t('cycUnknown')}</div>
          <div class="meter"><i style="width:0%"></i></div>
          <div class="meta">${t('cycNoFactory')}</div>`;
      } else if(info.neverStarted){
        state=`<div class="state-wait">${t('cycNeverStarted')}</div>
          <div class="meter"><i style="width:0%"></i></div>`;
      } else if(info.idle){
        state=`<div class="lbl" style="color:var(--alarm)">${t('cycIdleSince')}</div>
          <div class="meter"><i style="width:100%;background:var(--alarm)"></i></div>
          <div class="meta">${fmtDuration(info.idleMs)}</div>`;
      } else if(info.unknownSince){
        state=`<div class="state-wait">${t('inProduction')}</div>
          <div class="meter"><i style="width:100%;background:var(--ring-adv)"></i></div>
          <div class="meta">${t('cycProjectedHint')}</div>`;
      } else {
        state=`<div class="lbl" style="color:var(--ring-adv)">${t('inProduction')}</div>
          <div class="meter"><i style="width:${Math.round(info.cycle)}%;background:var(--ring-adv)"></i></div>
          <div class="meta">${fmtLeft(info.remainingMs)}</div>`;
      }
      // Вход — recipeInputs (data/recipes.json, проверено по источникам,
      // правило 2): ESI отдаёт, что фабрика производит, но не что
      // потребляет, а это уже известно и без ESI.
      const realInputs=recipeInputs[pin.product]||[];
      return `<div class="cols2">
        <div>${state}</div>
        <div><div class="lbl">${t('producesW')}</div>
          <div class="val">${pin.product
            ?`${ids[pin.product]?`<img src="${ICON(ids[pin.product])}" alt="" style="width:14px;height:14px;vertical-align:-3px;margin-right:3px">`:''}${pin.product}`
            :'—'}</div>
          ${realInputs.length?`<div class="lbl" style="margin-top:5px">${t('inputW')}</div>
          <div class="inputs">${realInputs.map(n=>ids[n]
            ?`<span class="have" title="${n}"><img src="${ICON(ids[n])}" alt=""></span>`
            :`<span title="${n}">·</span>`).join('')}</div>`:''}
          <div class="lbl" style="margin-top:5px">${t('capContent')}</div>
          ${contentsHTML(pin.contents)}</div>
      </div>`;
    }
    const inputs=(ctx.resIn||'').split(',').map(x=>x.trim()).filter(Boolean);
    return `<div class="cols2">
      <div><div class="state-wait">${t('cycUnknown')}</div>
        <div class="meter"><i style="width:0%"></i></div>
        <div class="meta">${ctx.real||!ctx.hasLoad?t('cycNoFactory'):t('afterGame')}</div></div>
      <div><div class="lbl">${t('producesW')}</div>
        <div class="val">${ctx.resOut||'—'}</div>
        ${inputs.length?`<div class="lbl" style="margin-top:5px">${t('inputW')}</div>
        <div class="inputs">${inputs.map(n=>ids[n]
          ?`<span class="have" title="${n}"><img src="${ICON(ids[n])}" alt=""></span>`
          :`<span title="${n}">·</span>`).join('')}</div>`:''}</div>
    </div>`;
  }
  return '';
}

/* Полоса структур панели — общая для плана и факта из игры, когда
   известен только агрегированный состав (structures_detail: kind+count,
   без поштучных данных ESI). Для факта из игры с пересинхронизированными
   пинами используется pinUnitsHTML() — там каждая строка настоящая. */
function structureUnitsHTML(structuresDetail, planetType, ctx){
  const units=[];
  (structuresDetail||[]).forEach(sd=>{ for(let i=0;i<sd.count;i++) units.push(sd.kind); });
  return units.map((kind,i)=>{
    const isCC=kind==='command_center';
    const code=`${String.fromCharCode(65+(i%26))}${i}-${ctx.planetLabel}`;
    // Единственный экстрактор колонии — однозначное сопоставление с
    // абстрактной строкой плана (в отличие от N одинаковых фабрик, где
    // непонятно, какая из них какая: ctx.ecuPin заполняется, только
    // когда экстрактор ровно один — см. openColony()).
    const unitCtx=(kind==='extractor_control_unit'&&ctx.ecuPin)?{...ctx,pin:ctx.ecuPin}:ctx;
    // Тот же res_out на всех фабричных иконках строки — см. структурную
    // полосу карточки (structOrbsForRow) с тем же обоснованием.
    const isFactoryKind=kind.endsWith('industry_facility');
    const orbOpts=isCC&&ctx.hasLoad?{cpu:ctx.cpu,pg:ctx.pg,size:52}
      :isFactoryKind&&ctx.resOut?{product:ctx.resOut,size:52}:{size:52};
    return `<div class="unit">
      ${structOrb(kind,1,planetType,orbOpts)}
      <div class="panel hud-corner">
        <div class="title">${sname(kind)} <span class="code">${code}</span></div>
        ${structureUnitFields(kind,unitCtx)}
      </div>
    </div>`;
  }).join('');
}

/* Одна строка — один настоящий пин (в отличие от structureUnitsHTML,
   где пины одного вида схлопнуты в count). Только для факта из игры,
   когда колония пересинхронизирована с pins (см. Colony.pins).

   load — {cpu,pg} настоящей загрузки командного центра этой колонии
   (realRows()/c.cpu_percent, см. scripts/sync_colony_status.py::
   real_colony_load()), либо null, если планеты нет в planet_industry.csv
   — тогда честное «нет данных», как и раньше. Фабрики заодно получают
   настоящий прогресс цикла (pinCycleInfo) вместо статичного пунктира. */
function pinUnitsHTML(pins, planetType, planetLabel, load, gameLastUpdate, routes){
  const sim=simulateColonyFactories(pins,routes,gameLastUpdate);
  return sortPinsForDisplay(pins).map((pin,i)=>{
    const code=`${String.fromCharCode(65+(i%26))}${i}-${planetLabel}`;
    const pinSim=sim&&sim.get(pin.pin_id);
    const info=pinCycleInfo(pin,gameLastUpdate,pinSim);
    const opts={size:52, cycle:info.cycle, idle:info.idle};
    if(pin.kind==='command_center' && load){ opts.cpu=load.cpu; opts.pg=load.pg; }
    return `<div class="unit">
      ${structOrb(pin.kind,1,planetType,opts)}
      <div class="panel hud-corner">
        <div class="title">${sname(pin.kind)} <span class="code">${code}</span></div>
        ${structureUnitFields(pin.kind,{hasLoad:!!load, cpu:load&&load.cpu, pg:load&&load.pg,
          cpuUsed:load&&load.cpuUsed, cpuCap:load&&load.cpuCap, pgUsed:load&&load.pgUsed, pgCap:load&&load.pgCap,
          pin, gameLastUpdate, sim:pinSim})}
      </div>
    </div>`;
  }).join('');
}

function openColony(id){
  if(String(id).startsWith('real:')) return openRealColonyDetail(id);
  const p=plan.find(x=>x.id===id); if(!p)return;
  currentColony=id;
  const planetId=ids['planet:'+p.planet_type];
  const real=matchColony(p);                        // реальная колония из ESI или null
  const char=crew.find(c=>c.name===p.character);    // синхронизированные скиллы персонажа
  const expMs=real&&real.nearest_expiry?Date.parse(real.nearest_expiry):null;
  const ecuPins=((real&&real.pins)||[]).filter(x=>x.kind==='extractor_control_unit');

  const body=structureUnitsHTML(p.structures_detail, p.planet_type, {
    hasLoad:true, cpu:p.cpu_percent, pg:p.pg_percent, pgb:p.pg_breakdown||{},
    resIn:p.res_in, resOut:p.res_out, real, expMs, planetLabel:romanNumeral(p.planet),
    ecuPin:ecuPins.length===1?ecuPins[0]:null,
  });

  document.getElementById('colonyPage').innerHTML=`
    <div class="cp-head">
      ${orb(p,58)}
      <div>
        <div class="pilot-name">${p.character}${char?` <span class="skills">CCU ${char.ccu} · IC ${char.ic}</span>`:''}</div>
        <div class="sub">
          <span class="sysbadge">${p.system} ${romanNumeral(p.planet)}</span>
          <span class="sec" style="color:${secColor(p.planet_type)}">${p.planet_type}</span>
          <span class="num">${nloc(Math.round(p.planet_radius_km))} ${t('km')}</span>
          <span>${roleLabel(p)}</span>
          ${real?`<span class="synced">${t('syncedColony')}${real.upgrade_level?` · CC${real.upgrade_level}`:''}</span>`:''}
        </div>
      </div>
      <button class="back" onclick="closeColony()">${t('back')}</button>
    </div>
    ${(p.shared_extraction_count||1)>1?`<div class="msg" style="margin:0 16px 8px">
      ${t('sharedExtract').replace('{n}',p.shared_extraction_count)}</div>`:''}
    ${body}`;
  fixTipOverflowIn(document.getElementById('colonyPage'));

  document.getElementById('dashBody').style.display='none';
  document.getElementById('shopping').style.display='none';
  document.getElementById('colonies').style.display='none';
  document.querySelector('#page-dash .tools').style.display='none';
  document.getElementById('colonyPage').classList.add('on');
}

/* Та же панель для реальной колонии без пары в плане — id вида
   «real:character_id:planet_id» (см. realRows()). CPU/Power командного
   центра считает сервер по настоящим данным (structures+links+heads из
   ESI, радиус — из planet_industry.csv), см. scripts/sync_colony_status.py
   ::real_colony_load(); null, если планеты нет в этом файле — тогда
   честное «нет данных», как и раньше. Остальное — реальные поштучные
   данные каждого пина (pinUnitsHTML), когда колония пересинхронизирована
   новым кодом (Colony.pins); до первой такой синхронизации — прежний
   агрегат. */
function openRealColonyDetail(id){
  const [, charId, planetId]=id.split(':');
  const c=coloniesData.find(x=>String(x.character_id)===charId && String(x.planet_id)===planetId);
  if(!c) return;
  currentColony=id;
  const ptype=c.planet_type?c.planet_type[0].toUpperCase()+c.planet_type.slice(1):'';
  const expMs=c.nearest_expiry?Date.parse(c.nearest_expiry):null;
  const row={isReal:true, character:c.character, system:c.system_name,
    planet:String(c.planet_index), planet_type:ptype, nearest_expiry:c.nearest_expiry,
    structures_detail:c.structures||[], pins:c.pins||[],
    role_key:(c.structures||[]).some(s=>s.kind==='extractor_control_unit')?'mine':
      (c.structures||[]).some(s=>s.kind.endsWith('industry_facility'))?'proc':null};
  const char=crew.find(x=>x.name===c.character);
  const load=c.cpu_percent!=null?{cpu:c.cpu_percent, pg:c.pg_percent,
    cpuUsed:c.cpu_used, cpuCap:c.cpu_capacity, pgUsed:c.pg_used, pgCap:c.pg_capacity}:null;

  const body=(c.pins&&c.pins.length)
    ? pinUnitsHTML(c.pins, ptype, romanNumeral(c.planet_index), load, c.game_last_update, c.routes)
    : structureUnitsHTML(c.structures, ptype, {hasLoad:!!load, cpu:load&&load.cpu, pg:load&&load.pg,
        real:c, expMs, planetLabel:romanNumeral(c.planet_index)});

  document.getElementById('colonyPage').innerHTML=`
    <div class="cp-head">
      ${orb(row,58)}
      <div>
        <div class="pilot-name">${c.character}${char?` <span class="skills">CCU ${char.ccu} · IC ${char.ic}</span>`:''}</div>
        <div class="sub">
          <span class="sysbadge">${c.system_name} ${romanNumeral(c.planet_index)}</span>
          <span class="sec" style="color:${secColor(ptype)}">${ptype}</span>
          <span class="synced">${t('syncedColony')}${c.upgrade_level?` · CC${c.upgrade_level}`:''} · ${pl(c.num_pins,'pPin')}</span>
        </div>
      </div>
      <button class="back" onclick="closeColony()">${t('back')}</button>
    </div>
    ${body}`;
  fixTipOverflowIn(document.getElementById('colonyPage'));

  document.getElementById('dashBody').style.display='none';
  document.getElementById('shopping').style.display='none';
  document.getElementById('colonies').style.display='none';
  document.querySelector('#page-dash .tools').style.display='none';
  document.getElementById('colonyPage').classList.add('on');
}

function closeColony(){
  currentColony=null;
  document.getElementById('colonyPage').classList.remove('on');
  document.getElementById('dashBody').style.display='';
  document.getElementById('shopping').style.display='';
  document.getElementById('colonies').style.display='';
  document.querySelector('#page-dash .tools').style.display='';
}
document.addEventListener('keydown',e=>{ if(e.key==='Escape')closeColony(); });

/* ── Справка и «О проекте» ────────────────────────────────────── */
let currentModal=null;

const DOC={
 help:{
  ru:{title:'Справка',html:`
   <h3 id="h-overview">Что считает планировщик</h3>
   <p>PI Director отвечает на один вопрос: сколько колоний и каких нужно, чтобы
   производить выбранные продукты непрерывно, и хватит ли на это ваших персонажей.</p>
   <p>Расчёт идёт сверху вниз: целевой продукт разворачивается до сырья через дерево
   рецептов, затем число фабрик переводится в число шаблонов застройки и колоний.
   Если выбрано несколько продуктов, общие компоненты учитываются один раз
   с суммарной потребностью, а не по максимуму.</p>
   <p><b>Данные по планетам сейчас загружены только для региона Fountain.</b>
   Созвездия, системы и плотности сырья для добычи доступны только в его
   пределах — расчёт для других регионов New Eden недоступен, пока для них
   нет такого же файла данных.</p>

   <h3 id="h-howto">Как пользоваться</h3>
   <ol>
    <li>На вкладке «Настройки» отметьте целевые продукты. Можно выбрать несколько
    сразу — список с поиском и иконками, отмеченное показано внизу. Кнопки P2/P3/P4
    над списком сужают его по тиру продукта, вместе с текстовым поиском, не вместо него.</li>
    <li>Поле «Линий на цепочку» (по умолчанию 1) умножает весь выбранный
    набор продуктов целиком — если персонажей больше, чем нужно для одной
    линии, панель «Персонажей больше, чем нужно» после расчёта подскажет
    точное число, на сколько ещё линий хватит пула, кнопкой «Взять»
    рядом с подсказкой.</li>
    <li>Отметьте созвездия для добычи (все — в регионе Fountain). Кнопка «Весь регион» отмечает все сразу;
    если в выгрузке есть колонка региона, созвездия сгруппированы по ним.
    Либо отметьте «Покупать весь P1 на бирже (не строить добычу)» —
    тогда созвездия можно не выбирать вовсе: план построит только
    перерабатывающие колонии, а стоимость закупки P1 (Jita, buy-ордер)
    появится отдельной строкой в прогнозе прибыльности.</li>
    <li>Задайте запас на истощение месторождений, если нужен. Это не расчёт,
    а поправка: плотность сырья у нас статичная, а в игре месторождения истощаются.</li>
    <li>Выберите домашнюю систему для заводов. Переработка ставится на
    предпочтительные Barren и Temperate (для P4 это правило игры, не просто
    предпочтение); среди них сначала выбираются планеты с меньшим налогом POCO,
    а при равном налоге — с меньшим радиусом (чем меньше планета, тем дешевле
    линки). Другие типы планет используются, только если Barren/Temperate по
    размеру не подошли — план честно об этом предупредит.</li>
    <li>Под выбором системы показан предел: до какого радиуса в неё
    помещается двойной шаблон и сколько планет системы ему подходят.
    Это видно до расчёта, а не после.</li>
    <li>Нажмите «Построить план». Результат появится на дашборде.</li>
   </ol>
   <p>Если ни одна планета системы не подходит под двойной шаблон, план не
   строится молча наполовину: появляется выбор — сменить домашнюю систему
   или ставить по одному шаблону на планету. Второй вариант работает, но
   планет и персонажей потребуется вдвое больше, поэтому решение за вами.</p>

   <h3 id="h-dashboard">Что показывает дашборд</h3>
   <div class="rows">
    <div>Три режима вида</div><div>Детальный со всеми структурами, список по персонажам
    и плотная сетка. Переключаются кнопками слева вверху, там же сортировка.</div>
    <div>Кольцо колонии</div><div>Загрузка по узкому ресурсу: сколько процентов ёмкости
    командного центра занято. Красное — запаса почти нет.</div>
    <div>Кольца структур</div><div>Внешнее кольцо — тип структуры, внутреннее — ход
    производственного цикла. Каждая фабрика показана отдельным кругом, как в игре.
    У командного центра кольцо двойное: бирюзовым CPU, красным Power.</div>
    <div>Кнопка «Детали»</div><div>Открывает разбор колонии: командный центр с полосами
    загрузки, причалы, склады, экстрактор и каждая фабрика со своими показателями.</div>
    <div>Список закупки</div><div>Внизу дашборда — сколько командных центров какого
    типа купить под этот план.</div>
    <div>Быстрые переходы</div><div>Кнопки «↑ План», «↓ Мои колонии» и «↓ Список закупки»
    в панели вида — прокручивают прямо к нужному блоку, не листая длинный план
    вручную. Появляются, только когда в соответствующем блоке есть что показать.</div>
    <div>Выгрузка в Excel</div><div>Кнопка в панели вида, доступна, если есть план или
    хотя бы одна синхронизированная колония. Для плана — три листа (план, сводка и
    проверка запаса; сводка считается формулами и пересчитывается, если править лист
    вручную), для настоящих колоний — отдельный лист «Мои колонии» тем же форматом
    (без сводки и проверки — командные центры уже куплены). Если построены и план, и
    колонии есть — оба набора листов в одной книге.</div>
   </div>

   <h3 id="h-staffing">Если персонажей не хватает или слишком много</h3>
   <p>Как только вы отметили целевые продукты, программа считает, поместится ли
   полный цикл в ваш пул. Учитываются не только слоты планет, но и прокачка:
   добывающий шаблон требует Command Center Upgrades IV, и «слотов хватает»
   ещё не значит «поместится».</p>
   <p>Если <b>не хватает персонажей</b>, предлагаются цепочки, которые поместятся
   целиком, по убыванию выгоды. Среди них могут быть продукты тиром ниже —
   это нормальный ответ: делать P3 непрерывно выгоднее, чем P4 с простоями.
   Можно и проигнорировать: кнопка «Строить как есть» построит план, который
   честно сообщит о дефиците сырья.</p>
   <p>Если <b>персонажей больше, чем нужно</b>, предлагаются цепочки выше тиром,
   помещающиеся в остаток, либо дополнительные линии того же или меньшего тира.
   Отказ тоже возможен: кнопка «Занять свободных добычей» пустит их на добычу
   сырья вашей же цепочки, начиная с самого дефицитного в выбранных
   констелляциях. Такие колонии помечены в плане как «Добыча (избыток)» —
   это не часть расчётной потребности, а загрузка простаивающих.</p>

   <h3 id="h-logistics">Логистика: почему колонии кучкуются</h3>
   <p>Добывающие колонии одного персонажа планировщик старается держать в одной
   системе. Разница практическая: шесть колоний в одной системе собираются за
   один заход, шесть в разных — за шесть перелётов.</p>
   <p>Для этого системы, где встречается сразу несколько нужных видов сырья,
   получают приоритет над системами с одним. Плотность остаётся вторым
   критерием: богатое месторождение в одиночной системе может перевесить.</p>
   <p>В карточке «Разброс по системам» видно, сколько систем в среднем приходится
   на персонажа. Значение около единицы означает, что урожай собирается компактно;
   если оно заметно больше двух, стоит присмотреться к набору созвездий.</p>

   <h3 id="h-colors">Как читать цвета</h3>
   <p>Цвет здесь не украшение, а способ не читать подписи. Плашка тира окрашена
   по переделу: холодные оттенки у сырья и P1, теплее к P3 и P4. Роль колонии
   тоже плашкой — бирюзовая добыча, оранжевая переработка.</p>
   <p>Числа окрашены по смыслу: зелёное значит запас есть, янтарное — на исходе,
   красное — предел. Лучшая строка в таблице выгоды отмечена янтарной полосой
   слева, как и самая напряжённая колония в плане.</p>

   <h3 id="h-saved">Сохранённые планы</h3>
   <p>План можно сохранить под именем и вернуться к нему позже: планы лежат на сервере,
   поэтому переживают перезагрузку и открываются с другого устройства.</p>
   <p>Сохранение и список планов привязаны к вашему входу через EVE SSO — без входа
   сохранить план нельзя, а после входа видны только планы, сохранённые тем же аккаунтом.</p>
   <p>Отметьте два плана галочками, чтобы сравнить планы между собой. Разница
   показывает не только числа, но и какие именно колонии появились или исчезли —
   «на три планеты меньше» не отвечает на вопрос, каких именно.</p>

   <h3 id="h-profit-ranking">Что выгоднее производить</h3>
   <p>Таблица на вкладке «Настройки» ранжирует цепочки по ISK на колонию в час,
   а не по цене за единицу: ограниченный ресурс здесь планеты и персонажи, а не время.
   Линия, приносящая больше всех в час, но занимающая вдвое больше планет, хуже.</p>
   <p>Налог POCO, доставка и биржевые сборы не учтены, поэтому числа — верхняя оценка.</p>

   <h3 id="h-profitability">Прогноз прибыльности плана</h3>
   <p>Панель на дашборде, сразу над построенным планом — не обобщённая
   экономика шаблона выше, а прибыльность УЖЕ ПОСТРОЕННОГО плана:
   настоящее число колоний и фабрик из расчёта, помесячно. В отличие от
   таблицы «Что выгоднее производить», здесь учтён налог POCO — и на
   вывоз, и на ввоз на каждом перемещении между планетами по цепочке,
   не только на финальной продаже.</p>
   <p>Ставка POCO берётся автоматически — точное значение КОНКРЕТНОЙ
   планеты из выгрузки данных региона, той же, на которой считается весь
   остальной план. Если ставка планеты неизвестна (планета за пределами
   загруженного региона), расчёт честно исключает такую планету, а не
   считает её беспошлинной. Чистая прибыль показывается только когда
   собраны все нужные цены и известны все нужные ставки — частичная
   сумма выглядела бы точнее, чем есть на самом деле.</p>
   <p>Учтена и пропускная способность причала: он вмещает фиксированный
   объём независимо от тира, но чем больше видов сырья туда попадает
   (обычно 3 у P3/P4 против 2 у P2) — тем быстрее он опустошается, и
   фабрика простаивает в ожидании новой партии, довезённой игроком.
   Простой каскадно передаётся дальше по цепочке: если сырьё довозится
   нестабильно (например, закупаемый P1), следующие тиры тоже не могут
   работать быстрее, чем оно поступает. Когда это заметно ограничивает
   цепочку, в панели появляется строка с реальной долей рабочего
   времени по тирам.</p>
   <p>Под верхней строкой чисел — три карточки в ряд: разбивка выручки
   по конечным продуктам (сколько единиц и на сколько ISK по снимку
   рыночных цен получится к концу месяца по каждому виду), список
   закупки сырья P1 (только в режиме «покупать P1 на бирже» — см. ниже)
   и допущения/предупреждения плана.</p>
   <p>Если один из выбранных целевых продуктов одновременно нужен
   ДРУГОМУ выбранному целевому продукту как сырьё (например, выбраны и
   промежуточный, и конечный продукт одной цепочки, каждый своей
   отдельной линией), в выручке учитывается только его СОБСТВЕННАЯ
   прямая целевая доля — часть построенных колоний, а не всё их
   количество: остальное физически уходит на переработку в следующий
   продукт. Один и тот же материал не считается продан дважды и не
   теряется полностью — само распределение видно по числам в карточке
   «Выручка по продуктам», отдельного примечания под ней для этого
   нет.</p>

   <h3 id="h-purchase-list">Список закупки сырья (P1)</h3>
   <p>Панель под прогнозом прибыльности — показывается только в режиме
   «покупать P1 на бирже»: по каждому виду сырья P1 — сколько единиц
   нужно закупать в месяц и цена на момент построения плана (тот же
   снимок рыночных цен, что и у прогноза прибыльности выше, не
   пересчитывается заново при каждом открытии страницы). Строка без
   цены в снимке помечена «нет цены», а не подставляет ноль. Тот же
   список попадает в экспорт в Excel отдельным листом «Закупка P1» с
   теми же ценами, что были на экране при построении плана.</p>

   <h3 id="h-indicators">Индикаторы вверху</h3>
   <p>Плашка «ESI» — число игроков онлайн из снимка сервера; жёлтая точка значит,
   что снимок давно не обновлялся (сборщик мог не запуститься), при наведении
   видно, сколько минут назад он был собран.</p>
   <p>Плашка «сборщики» — здоровье фоновых задач, которые собирают все данные
   приложения по расписанию (правило проекта: сама страница в сеть не ходит).
   Зелёная точка — все идут по расписанию; жёлтая — какая-то не запускалась
   дольше двух своих интервалов (планировщик мог остановиться); красная —
   какая-то падает несколько раз подряд. При наведении — список задач с
   временем последнего запуска и числом сбоев для тех, что падают. Плашка
   пропадает на узких окнах вместе с плашкой «данные» — это диагностика для
   владельца, а не то, что нужно каждому посетителю.</p>

   <h3 id="h-theme-lang">Тема и язык</h3>
   <p>В верхней панели переключаются светлая и тёмная тема, а также русский
   и английский язык. Выбор запоминается в браузере: это предпочтения отображения,
   им место на вашем устройстве, в отличие от планов и персонажей.</p>

   <h3 id="h-eve-sso">Вход через EVE Online</h3>
   <p>Кнопка «Войти через EVE SSO» в шапке подключает по официальному
   протоколу CCP только первого персонажа — как только связан хотя бы
   один, кнопка пропадает из шапки. Добавить ещё персонажей после
   этого можно только на вкладке «Настройки», в карточке «Где
   перерабатываем» → «Персонажи», кнопкой «Добавить персонажа». После
   входа (первого и любого следующего персонажа) приложение открывается
   именно на вкладке «Настройки», не «Дашборд» — здесь же сразу видно
   результат. Сразу после входа планировщик подтягивает настоящие уровни скиллов
   (Command Center Upgrades и Interplanetary Consolidation) и реальные
   колонии — не нужно ждать ближайшего планового обновления, обычно
   это занимает несколько секунд. Токены хранятся на сервере в
   зашифрованном виде; приложение не видит ваш пароль. Персонажи,
   колонии и планы привязаны к вашему входу — другие пользователи
   приложения их не видят.</p>
   <p>Кнопка «Отвязать» рядом с персонажем в списке слева стирает его токен
   на нашей стороне — планировщик перестаёт видеть персонажа, пока вы не
   войдёте им заново. Отозвать доступ самому приложению целиком можно
   на eveonline.com/account/third_party_apps.</p>
   <p>Если у персонажа истёк или отозван доступ (например, сменили пароль
   EVE или отозвали приложение на eveonline.com), рядом с ним появится
   красная кнопка «Перезайти»: планировщик больше не может обновлять его
   скиллы и колонии, пока вы не войдёте этим же персонажем заново через
   EVE SSO.</p>
   <p>Персонажи, вошедшие в одном браузере, группируются автоматически:
   первый вошедший становится основным сам себе, каждый следующий в том же
   визите подтягивается к нему без отдельных действий. При входе ЛЮБЫМ
   персонажем этой группы на любом устройстве или в любом браузере
   подтянутся сразу все остальные, без повторного входа каждым. Если
   автоматика выбрала не того основным — у любого альта в карточке
   персонажей есть кнопка «Сделать основным».</p>

   <h3 id="h-ingame">Мои колонии в игре</h3>
   <p>Отдельный блок на дашборде: что у персонажей реально построено — снимок
   из игры, а не расчётный план. Сводная строка над блоком считает четыре
   категории: сколько колоний простаивает (у добычи — программа экстрактора
   закончилась; у переработки — закончилось сырьё в причале), сколько
   требуют внимания (у добычи — причал забит под завязку), сколько в
   дефиците добычи, и общее число планет. Простаивает и требует внимания —
   независимые категории, колония может попасть в обе сразу.</p>
   <p>Карточка колонии выглядит как у расчётного плана, только состав
   берётся из настоящих пинов на планете. Кнопка «Детали» открывает полную
   панель: причал, склад и хранилище командного центра — что внутри и
   сколько это в процентах от вместимости; загрузка CPU/Power — в процентах
   и в абсолютных числах, как в игре; экстрактор — сколько голов, что
   добывает и с какой скоростью («сейчас» и «в среднем» по истории).</p>
   <p>ESI обновляет состояние фабрики только при заходе в колонию в игровом
   клиенте, поэтому программа честно проецирует его вперёд по маршрутам и
   накопленному сырью: «в производстве» или «простаивает» с настоящей
   длительностью, а для переработки — ещё и время, когда закончится сырьё в
   причале. Без маршрутов или сразу после пересинхронизации — честное
   «неизвестно», а не выдуманное состояние.</p>
   <p>Колония, добывающая меньше 48 000 ед./ч, помечена как дефицитная —
   на такой скорости ресурса не хватит на всю цепочку.</p>

   <h3 id="h-colonies-profit">Прогноз прибыльности по факту</h3>
   <p>Панель над списком колоний в игре — считает налог POCO так же, как и
   в расчётном плане, но по настоящей текущей скорости каждой колонии, не по
   теоретическому максимуму; налог на ввоз между разными колониями не
   считается — ESI не отдаёт, какая колония кормит какую.</p>

   <h3 id="h-honest-gaps">Чего программа пока не знает</h3>
   <p><b>Регион.</b> Данные о планетах есть только для Fountain — ни ESI,
   ни статический дамп CCP плотность сырья по планетам не отдают вообще
   (сервер считает её процедурно и не публикует), единственный способ
   получить такие данные для другого региона — сканирование в игре или
   сторонняя проверенная база. Расчёт плана для любого другого региона
   New Eden сейчас невозможен, не только неточен.</p>
   <p>Плотность сырья берётся из статического файла и считается постоянной. В игре
   месторождения истощаются, но живых данных об этом нет ни у кого, кроме самой игры.</p>`},

  en:{title:'Help',html:`
   <h3 id="h-overview">What the planner computes</h3>
   <p>PI Director answers one question: how many colonies of which kind you need to
   produce the selected products continuously, and whether your characters suffice.</p>
   <p>It works top-down: the target product is expanded to raw materials through the
   recipe tree, then factory counts become template and colony counts. With several
   targets, shared components are counted once with summed demand, not by maximum.</p>
   <p><b>Planet data is currently loaded for the Fountain region only.</b>
   Constellations, systems and extraction densities are only available within
   it — the plan cannot be calculated for any other New Eden region until the
   same kind of data file exists for it.</p>

   <h3 id="h-howto">How to use it</h3>
   <ol>
    <li>On the Settings tab tick your target products. Several products at once is fine —
    the list has search and icons, and your picks are shown below it. The P2/P3/P4 buttons
    above the list narrow it by product tier, alongside the text search, not instead of it.</li>
    <li>The “Lines per chain” field (1 by default) multiplies the whole
    selected set of products at once — if you have more characters than
    one line needs, the “More characters than needed” panel tells you
    after the calculation exactly how many more lines the pool can take,
    with a “Take” button right next to it.</li>
    <li>Tick the constellations you extract from (all of them are in Fountain). “Whole
    region” ticks them all; if your export has a region column, constellations are
    grouped by region.
    Or tick “Buy all P1 on the market (do not build extraction)” instead —
    then you don't need to pick constellations at all: the plan builds
    only processing colonies, and the P1 purchase cost (Jita buy order)
    shows up as its own line in the profitability forecast.</li>
    <li>Set a depletion safety margin if you need one. It is not a calculation but a
    correction: our resource density is static, while deposits deplete in game.</li>
    <li>Pick a home system for the factories. Processing goes on the preferred
    Barren and Temperate types (mandatory for P4 — a game rule, not just a
    preference); among those, the lowest POCO tax rate wins first, and radius
    (smaller is cheaper on links) only breaks ties on equal tax. Other planet
    types are used only when no Barren/Temperate planet fits by size — the plan
    warns you honestly when that happens.</li>
    <li>Below the system picker you see the limit: up to which radius a double
    template fits planets here, and how many of the system's planets qualify.
    This is shown before the calculation, not after.</li>
    <li>Press “Build plan”. The result appears on the Dashboard.</li>
   </ol>
   <p>If no planet in the system fits a double template, the plan is not quietly
   half-built: you get a choice — pick another home system, or use a single
   template per planet. The latter works, but needs twice as many planets and
   characters, so the decision is yours.</p>

   <h3 id="h-dashboard">What the dashboard shows</h3>
   <div class="rows">
    <div>Three view modes</div><div>Detailed with every structure, a per-character list
    and a dense grid. Switch with the buttons at the top left, sorting is there too.</div>
    <div>Colony ring</div><div>Load on the binding resource: how much command centre
    capacity is used. Red means almost no headroom.</div>
    <div>Structure rings</div><div>The outer ring is the structure type, the inner one is
    cycle progress. Every factory is drawn as its own circle, as in game. The command
    centre has a double ring: teal for CPU, red for Power.</div>
    <div>“Details” button</div><div>Opens the colony breakdown: command centre with load
    bars, launchpads, storage, the extractor and every factory with its own readings.</div>
    <div>Shopping list</div><div>At the bottom of the dashboard: how many command centres
    of each type this plan needs.</div>
    <div>Quick jump</div><div>The "↑ Plan", "↓ My colonies" and "↓ Shopping list" buttons in
    the view bar scroll straight to that block instead of paging through a long plan by
    hand. They only appear when that block actually has something to show.</div>
    <div>Excel export</div><div>Button in the view bar, enabled once there is a plan or
    at least one synced colony. For a plan — three sheets (plan, summary and headroom
    check; the summary uses formulas, so it recalculates if you edit the sheet by hand),
    for real colonies — a separate "My colonies" sheet in the same format (no summary or
    check — the command centres are already bought). With both a plan and colonies, the
    workbook has both sets of sheets.</div>
   </div>

   <h3 id="h-staffing">If you have too few or too many characters</h3>
   <p>As soon as you tick the target products, the app checks whether a full cycle
   fits your pool. It counts not only planet slots but skills: the extraction
   template needs Command Center Upgrades IV, so “enough slots” does not yet
   mean “it fits”.</p>
   <p>If there are <b>not enough characters</b>, you are offered chains that fit
   entirely, ranked by profit. Some may be a lower tier — that is a sound answer:
   running P3 continuously beats P4 with idle time. You can ignore it: “Build as
   is” produces a plan that honestly reports the raw material shortage.</p>
   <p>If there are <b>more characters than needed</b>, you are offered higher-tier
   chains that fit the remainder, or extra lines of the same or lower tier. You can
   decline too: “Put spare on extraction” sends them to mine your own chain's raw
   materials, starting with the scarcest in the chosen constellations. Such colonies
   are marked “Extraction (surplus)” — they are not part of the computed demand but
   a way to keep idle characters busy.</p>

   <h3 id="h-logistics">Logistics: why colonies cluster</h3>
   <p>The planner tries to keep one character's extraction colonies in the same
   system. The difference is practical: six colonies in one system are collected in
   a single trip, six in different systems take six trips of travel.</p>
   <p>To that end, systems holding several of the needed raw materials outrank systems
   holding just one. Density remains the second criterion: a rich deposit in a lone
   system can still win.</p>
   <p>The “Systems per character” card shows the average. A value near one means the
   harvest is compact; noticeably above two is a hint to revisit your constellations.</p>

   <h3 id="h-colors">How to read the colours</h3>
   <p>Colour here is not decoration but a way to avoid reading labels. The tier badge
   is coloured by processing stage: cool shades for raw materials and P1, warmer
   towards P3 and P4. The colony role is a badge too — teal for extraction,
   orange for processing.</p>
   <p>Numbers are coloured by meaning: green means there is headroom, amber means it
   is running out, red means the limit. The best row in the profitability table is
   marked with an amber bar on the left, as is the most strained colony in the plan.</p>

   <h3 id="h-saved">Saved plans</h3>
   <p>A plan can be saved under a name and reopened later: saved plans live on the server,
   so they survive a reload and open from another device.</p>
   <p>Saving and the plan list are tied to your EVE SSO login — you need to be logged in
   to save a plan, and once logged in you only see plans saved under the same account.</p>
   <p>Tick two of them to compare plans. The difference shows not only numbers but which
   colonies appeared or disappeared — “three planets fewer” does not say which ones.</p>

   <h3 id="h-profit-ranking">What is most profitable to produce</h3>
   <p>The table on the Settings tab ranks chains by ISK per colony-hour rather than by
   unit price: the limited resource here is planets and characters, not time. A line that
   earns most per hour but occupies twice the planets is worse.</p>
   <p>POCO tax, hauling and broker fees are not included, so the figures are an upper bound.</p>

   <h3 id="h-profitability">Plan profitability forecast</h3>
   <p>A panel on the dashboard, right above the built plan — not the generic
   template economics above, but the profitability of the ALREADY BUILT
   plan: the real number of colonies and factories from the calculation,
   monthly. Unlike the "most profitable to produce" table, this one
   accounts for POCO tax — on both export and import at every move between
   planets in the chain, not only on the final sale.</p>
   <p>The POCO rate is filled in automatically — the exact value for that
   SPECIFIC planet, from the same regional data export the rest of the plan
   is built on. If a planet's rate is unknown (a planet outside the loaded
   region), the calculation honestly excludes that planet rather than
   treating it as tax-free. Net profit is only shown once every needed
   price is available and every needed rate is known — a partial sum would
   look more accurate than it really is.</p>
   <p>Causeway throughput is accounted for too: it holds a fixed volume
   regardless of tier, but the more kinds of raw material it holds
   (usually 3 for P3/P4 versus 2 for P2), the faster it empties, and the
   factory idles waiting for a new batch hauled in by the player. That
   downtime cascades further down the chain: if a raw material arrives
   unevenly (say, purchased P1), the following tiers can't run any faster
   than it actually arrives. When this noticeably limits the chain, the
   panel shows a line with the real share of working time per tier.</p>
   <p>Below the top row of numbers are three cards side by side: a
   breakdown of revenue by final product (how many units and how much
   ISK, by the market price snapshot, each one is expected to bring in
   by month's end), the P1 raw material purchase list (only in "buy all
   P1 on the market" mode — see below), and the plan's assumptions and
   warnings.</p>
   <p>If one of the selected target products is also needed by ANOTHER
   selected target product as a raw material (say you picked both an
   intermediate and a final product from the same chain, each its own
   line), only its OWN direct target share counts as revenue — the rest
   of the colonies built for it physically go into processing the next
   product. The same material is neither counted twice nor dropped
   entirely — the split itself shows up in the numbers in the "Revenue
   by product" card, there is no separate note about it underneath.</p>

   <h3 id="h-purchase-list">Raw material purchase list (P1)</h3>
   <p>A panel below the profitability forecast — shown only in "buy all P1 on the
   market" mode: for each P1 raw material, how many units to buy per month, with
   prices as of plan build (the same market price snapshot as the profitability
   forecast above — it is not re-fetched every time the page opens). A product missing
   a price in the snapshot is marked "no price" rather than shown as zero. The same
   list is included in the Excel export as its own "P1 purchase" sheet, with the same
   prices that were on screen when the plan was built.</p>

   <h3 id="h-indicators">Top bar indicators</h3>
   <p>The "ESI" chip shows online players from a server snapshot; an amber dot means the
   snapshot hasn't updated in a while (the collector may not be running) — hover to see
   how many minutes old it is.</p>
   <p>The "jobs" chip shows the health of the background jobs that collect all of the
   app's data on a schedule (project rule: the page itself never reaches out to the
   network). Green — everything runs on schedule; amber — something hasn't run in over
   twice its own interval (the scheduler may have stopped); red — something is failing
   repeatedly. Hovering lists every job with when it last ran and, for failing ones, how
   many times in a row. The chip disappears on narrow windows along with the "data" chip
   — this is diagnostics for the app's owner, not something every visitor needs.</p>

   <h3 id="h-theme-lang">Theme and language</h3>
   <p>The top bar switches between light and dark theme and between Russian and English
   language. The choice is remembered in your browser: these are display preferences and
   belong on your device, unlike plans and characters.</p>

   <h3 id="h-eve-sso">Log in with EVE Online</h3>
   <p>The "Log in with EVE SSO" button in the top bar connects only your first
   character through CCP's official protocol — once at least one is linked, the
   button disappears from the top bar. To add more characters after that, go to
   the Settings tab, in the "Where to process" card → "Characters", and use the
   "Add character" button there. After logging in (the first character or any
   next one) the app opens directly on the Settings tab, not the Dashboard —
   you see the result right there. Right after you log in the planner pulls your
   real skill levels (Command Center Upgrades and Interplanetary Consolidation)
   and your real colonies — no need to wait for the next scheduled refresh, it
   usually takes a few seconds. Tokens are stored encrypted on the server; the
   app never sees your password. Characters, colonies and plans are tied to your
   login — other users of the app cannot see them.</p>
   <p>The "Unlink" button next to a character in the list on the left erases that
   character's token on our side — the planner stops seeing them until you log in
   again. You can revoke the whole app's access at
   eveonline.com/account/third_party_apps.</p>
   <p>If a character's access has expired or been revoked (say, you changed your EVE
   password or revoked the app on eveonline.com), a red "Reconnect" button appears next to
   them: the planner can no longer refresh their skills and colonies until you log in again
   with that same character through EVE SSO.</p>
   <p>Characters that log in from the same browser are grouped automatically: the first
   one to log in becomes primary to itself, and every next one in that same visit joins it
   with no extra steps. Logging in with ANY character from that group, on any device or
   browser, pulls in all the others at once, with no need to log in with each separately.
   If the automatic pick got it wrong, any alt in the character card has a "Make primary"
   button.</p>

   <h3 id="h-ingame">My in-game colonies</h3>
   <p>A separate dashboard panel: what your characters have actually built — a live
   snapshot from the game, not the computed plan. The summary line above it counts four
   things: how many colonies are idle (extraction — the extractor program has ended;
   processing — the launchpad has run out of raw material), how many need attention
   (extraction — the launchpad is packed full), how many are in extraction deficit, and
   the total planet count. Idle and needs-attention are independent — a colony can land
   in both at once.</p>
   <p>A colony card looks like a plan card, except the composition comes from the real
   pins on the planet. The Details button opens the full panel: launchpad, storage and
   the command centre's own hold — what's inside and how full as a percentage; CPU/Power
   load as a percentage and as absolute numbers, same as in the game; the extractor's
   head count, output and rate ("now" and "average" from history).</p>
   <p>ESI only updates a factory's state when the colony is opened in the game client, so
   the app honestly projects it forward from the routes and accumulated material — "in
   production" or "idle" with a real duration, and for processing, also when the
   launchpad will run out of material. Without routes or right after a resync, it says an
   honest "unknown" instead of guessing.</p>
   <p>A colony extracting under 48,000 units/hour is flagged as in deficit — at that rate
   the resource won't cover the whole chain.</p>

   <h3 id="h-colonies-profit">Actual profitability forecast</h3>
   <p>The panel above the in-game colony list — accounts for POCO tax the same way the
   plan version does, but by each colony's real current rate, not the theoretical maximum;
   import tax between different colonies isn't counted — ESI doesn't report which colony
   feeds which.</p>

   <h3 id="h-honest-gaps">What the program does not know yet</h3>
   <p><b>Region.</b> Planet data only exists for Fountain — neither ESI nor CCP's static
   dump exposes resource density for planets at all (the server computes it procedurally
   and never publishes it), so the only way to get such data for another region is
   in-game scanning or a verified third-party database. Building a plan for any other
   New Eden region isn't just inaccurate right now, it's impossible.</p>
   <p>Resource density comes from a static file and is treated as constant. Deposits do
   deplete in game, but nobody outside the game has live data on that.</p>`}},

 about:{
  ru:{title:'О проекте',html:`
   <h3>Программа</h3>
   <p>PI Director — планировщик планетарного производства для EVE Online. Считает состав
   колоний, распределяет их по персонажам и показывает, где план упирается в ограничения.</p>
   <dl>
    <dt>Версия</dt><dd id="aboutVersion">—</dd>
    <dt>Разработчик</dt><dd>mefffodiy-n</dd>
    <dt>Лицензия</dt><dd>MIT</dd>
    <dt>Исходный код</dt><dd><a href="https://github.com/mefffodiy-n/EVE-PI-app" target="_blank" rel="noopener">github.com/mefffodiy-n/EVE-PI-app</a></dd>
   </dl>

   <h3>Откуда данные</h3>
   <p>Все числовые константы взяты из проверенных источников и сверены между собой:
   игровые JSON-шаблоны застройки (набор
   <a href="https://github.com/DalShooth/EVE_PI_Templates" target="_blank" rel="noopener">EVE_PI_Templates</a>
   авторства <a href="https://github.com/DalShooth" target="_blank" rel="noopener">DalShooth</a>),
   справочник eve-webtools.com и вики EVE University. Полная сверка 68 продуктов с вики
   дала ноль расхождений.</p>
   <p>При споре о составе продукта приоритет у шаблонов застройки: их маршруты ссылаются
   на игровые type_id, а не на названия, в которых встречаются опечатки.</p>
   <p>Плотности сырья и радиусы планет — отдельный источник, охватывающий только регион
   Fountain: такие данные не отдаёт ни ESI, ни статический дамп CCP, единственный способ
   их получить — сканирование планет в игре.</p>

   <h3>Обращения к игре</h3>
   <p>Приложение не обращается к ESI по действию пользователя. Всё, что требует сети,
   собирают фоновые скрипты по расписанию, а страницы читают готовые снимки. Это защищает
   от исчерпания лимитов при большом числе пользователей. Исключение — вход через EVE SSO:
   обмен кода на токен по своей природе синхронный, но это разовое действие.</p>
   <p>Запросы соблюдают правила CCP: приложение представляется в User-Agent, передаёт
   дату совместимости, следит за лимитом ошибок и уважает просьбу подождать. Авторизация —
   OAuth 2.0 с PKCE; токены шифруются перед записью в базу.</p>

   <h3>Права</h3>
   <p>Автор — mefffodiy-n. Проект сделан для себя и выложен открыто: пользоваться,
   изменять и распространять его может кто угодно на условиях лицензии MIT.</p>
   <p>EVE Online® и Fenris Creations™, а также все связанные с ними логотипы и другие
   элементы являются товарными знаками Fenris Creations.<br>Приложение сделано
   независимо, Fenris Creations™ его не поддерживает и не одобряет.</p>`},

  en:{title:'About',html:`
   <h3>The program</h3>
   <p>PI Director is a planetary industry planner for EVE Online. It computes colony
   layouts, assigns them to characters and shows where the plan hits its limits.</p>
   <dl>
    <dt>Version</dt><dd id="aboutVersion">—</dd>
    <dt>Developer</dt><dd>mefffodiy-n</dd>
    <dt>License</dt><dd>MIT</dd>
    <dt>Source code</dt><dd><a href="https://github.com/mefffodiy-n/EVE-PI-app" target="_blank" rel="noopener">github.com/mefffodiy-n/EVE-PI-app</a></dd>
   </dl>

   <h3>Where the data comes from</h3>
   <p>Every constant comes from verified sources cross-checked against each other: the
   game's own JSON colony templates (the
   <a href="https://github.com/DalShooth/EVE_PI_Templates" target="_blank" rel="noopener">EVE_PI_Templates</a>
   set by <a href="https://github.com/DalShooth" target="_blank" rel="noopener">DalShooth</a>),
   the eve-webtools.com reference and the EVE University wiki. A full comparison of all
   68 products against the wiki found zero discrepancies.</p>
   <p>When sources disagree on a product's inputs, the colony templates win: their routes
   reference game type_ids rather than names, and names carry typos.</p>
   <p>Resource densities and planet radii are a separate source covering the Fountain
   region only: neither ESI nor CCP's static dump exposes that data, the only way to get
   it is scanning planets in-game.</p>

   <h3>Calls to the game</h3>
   <p>The application never calls ESI in response to a user action. Everything that needs
   the network is collected by scheduled background scripts, and pages read finished
   snapshots. This keeps rate limits safe as the number of users grows. The one exception
   is the EVE SSO login: exchanging the code for a token is inherently synchronous, but
   it is a one-off action.</p>
   <p>Requests follow CCP's rules: the app identifies itself in the User-Agent, sends a
   compatibility date, watches the error limit and respects requests to back off.
   Authentication is OAuth 2.0 with PKCE; tokens are encrypted before they are stored.</p>

   <h3>Rights</h3>
   <p>Author — mefffodiy-n. This project was built for personal use and released
   openly: anyone may use, modify and redistribute it under the MIT license.</p>
   <p>EVE Online® and Fenris Creations™ and all related logos and other elements are
   trademarks of Fenris Creations.<br>This application is made independently, and
   Fenris Creations™ does not support or endorse it.</p>`}}
};

/* anchor — необязательный id раздела (h-...) внутри doc.html: контекстные
   иконки-якорики в интерфейсе (helpTag()) открывают справку сразу на
   нужном разделе, а не с самого начала простыни. Без anchor поведение
   не меняется — открывается с начала, как раньше. */
function openModal(name, anchor){
  currentModal=name;
  const doc=DOC[name][lang]||DOC[name].ru;
  document.getElementById('modalTitle').textContent=doc.title;
  const body=document.getElementById('modalBody');
  body.innerHTML=doc.html;
  // Оглавление — не хардкод в DOC.help.ru/en (два языка расходились бы
  // при любой правке разделов), а список ссылок, собранный из уже
  // отрисованных <h3 id="h-...">: закрывает «справка — простыня» даже
  // для разделов без своей иконки в интерфейсе (helpTag()) — там
  // единственный вход в конкретный раздел.
  const headings=[...body.querySelectorAll('h3[id]')];
  if(headings.length){
    const toc=document.createElement('nav');
    toc.className='modal-toc';
    toc.innerHTML=headings.map(h=>`<a href="#${h.id}">${h.textContent}</a>`).join('');
    body.prepend(toc);
  }
  const v=document.getElementById('aboutVersion');
  if(v) v.textContent=meta.version||'—';
  document.getElementById('modal').classList.add('on');
  if(anchor){
    const target=document.getElementById(anchor);
    if(target) target.scrollIntoView({block:'start'});
  } else {
    body.scrollTop=0;
  }
}
function closeModal(){ document.getElementById('modal').classList.remove('on'); currentModal=null; }

/* Маленькая иконка-якорь у панели/поля интерфейса — один клик открывает
   справку сразу на разделе, который её объясняет (anchor — id вида
   "h-..." внутри DOC.help.ru/en, см. openModal()). Один и тот же id в
   обоих языках справки — при переключении языка ссылка остаётся верной. */
function helpTag(anchor){
  return `<button type="button" class="help-tag" onclick="openModal('help','${anchor}')" title="${t('help')}">?</button>`;
}

/* ── Состояние сервера и персонажи ────────────────────────────── */
async function loadMeta(){
  try{
    meta=await (await fetch(`${API}/meta`)).json();
  }catch(e){ meta={_failed:true}; }
  renderMeta();
}
/* Отрисовка из сохранённого meta — вызывается и при смене языка,
   иначе чип «в игре / online» остаётся на языке первой загрузки. */
function renderMeta(){
  const chip=document.getElementById('chipEsi'); if(!chip) return;
  const dot=chip.querySelector('.dot');
  const v=document.getElementById('appVersion'); if(v) v.textContent=meta.version||'—';
  const sv=meta.server||{};
  if(meta._failed){ document.getElementById('esiPlayers').textContent=t('noData'); return; }
  if(sv.available && sv.players!=null){
    dot.className='dot'+(sv.stale?' stale':' live');
    document.getElementById('esiPlayers').textContent=`${nloc(sv.players)} ${t('online')}`;
    chip.title=sv.stale?`${t('snapshotMade')} ${sv.age_minutes} ${t('minutesAgo')}`:'';
  } else {
    dot.className='dot';
    document.getElementById('esiPlayers').textContent=t('noData');
    chip.title=sv.hint||'';
  }
  renderJobsChip();
}

/* Статус фоновых сборщиков (scripts/scheduler.py) — отдельный процесс
   от веба, поэтому /api/meta отдаёт снимок, который планировщик пишет
   сам после каждого запуска джоба (api/blueprints/meta.py::_job_status).
   Подсказка при наведении перечисляет каждый джоб — когда запускался в
   последний раз и сколько раз подряд падает, а не просто «что-то не
   так»: агрегированный «худший» статус красит одну точку, но без
   разбивки непонятно, чинить ли вообще что-то прямо сейчас. */
/* Ключ джоба (scripts/scheduler.py::Job.key) → ключ словаря T. Имя джоба
   раньше приходило с сервера уже по-русски (j.name) — единственное место
   в приложении, где правило 9 (весь текст переводим) не соблюдалось.
   Старый снимок без key (до перезапуска планировщика) — откат на j.name. */
const JOB_LABEL_KEYS={
  server_status:'jobServerStatus',market_prices:'jobMarketPrices',
  refresh_tokens:'jobRefreshTokens',sync_skills:'jobSyncSkills',
  sync_colonies:'jobSyncColonies',backup:'jobBackup',refresh_sde:'jobRefreshSde',
};
function renderJobsChip(){
  const chip=document.getElementById('chipJobs'); if(!chip) return;
  const dot=chip.querySelector('.dot');
  const js=meta.jobs||{};
  const label=document.getElementById('jobsStatus');
  if(!js.available){
    dot.className='dot';
    label.textContent=t('noData');
    chip.title='';
    return;
  }
  dot.className='dot'+(js.worst==='failing'?' failing':js.worst==='stale'?' stale':' live');
  label.textContent=js.worst==='ok'?t('jobsOk'):js.worst==='stale'?t('jobsStale'):t('jobsFailing');
  chip.title=(js.jobs||[]).map(j=>{
    const age=j.age_minutes!=null?`${j.age_minutes} ${t('minutesAgo')}`:t('jobsNever');
    const fail=j.status==='failing'?` — ${fmtPlain(t('jobsFailingCount'),{n:j.failures})}`:'';
    const label=JOB_LABEL_KEYS[j.key]?t(JOB_LABEL_KEYS[j.key]):j.name;
    return `${label}: ${age}${fail}`;
  }).join('\n');
}

/* Тот же {ключ}-шаблон, что и fmt(), но без <b> — для title (plain text). */
function fmtPlain(template,values){
  return Object.entries(values).reduce((s,[k,v])=>s.replace(`{${k}}`,v),template);
}

async function loadCrew(){
  try{
    const d=await (await fetch(`${API}/characters`)).json();
    crew=d.characters||[];
  }catch(e){ crew=[]; }
  renderCrew(); loadThresholds();
  // loadColonies() бежит независимо и может успеть раньше — тогда
  // аватары (charIdOf смотрит в crew) отрисовались бы без портретов.
  // Перерисовать план и факт, раз crew уже есть.
  renderDash(); renderColonies();
}

/* Кнопка «Admin-панель» рядом с «Добавить персонажа» — только если
   ТЕКУЩАЯ сессия проходит серверную проверку доступа (28.09.2026, по
   прямому запросу пользователя). Один раз при загрузке страницы —
   admin-доступ персонажа не меняется в рамках визита без полной
   перезагрузки (вход другим персонажем идёт через SSO-редирект,
   handleAuthRedirect(), а это всегда свежая загрузка страницы). 404 —
   такой же обычный ответ, как и «нет доступа», не ошибка сети. */
async function checkAdminAccess(){
  try{
    const r=await fetch(`${API}/admin/ping`);
    isAdmin=r.status===200;
  }catch(e){ isAdmin=false; }
  renderCrew();
}

/* «Отвязать персонажа». Стирает нашу копию токена и открепляет персонажа
   от визита — планировщик и «Мои колонии» его больше не увидят, пока не
   войдёте им заново. Гарантировано всегда. Настоящий отзыв на стороне
   CCP — только если ответ несёт revoked:true (api/blueprints/auth.py::
   unlink пытается его, только когда владелец приложения задал секрет —
   без секрета публичный PKCE-клиент вызвать /v2/oauth/revoke не может).
   Сообщение честно называет, что произошло на самом деле, а не что
   должно было произойти (правило 1). Без confirm(): само действие
   обратимо повторным входом, а не постоянная потеря данных. */
async function unlinkCharacter(id){
  const r=await fetch(`${API}/auth/unlink/${id}`, {method:'POST'});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){
    msg(d.message||t('unlinkFail'), true);
    return;
  }
  msg(d.revoked?t('unlinkedRevoked'):t('unlinkedLocalOnly'));
  await loadCrew();
}

async function startLogin(){
  const r=await fetch(`${API}/auth/login-url`);
  const d=await r.json();
  if(d.status==='success' && d.url){ location.href=d.url; return; }
  alert(d.message||t('ssoUnavailable'));
}

/* ── Настройки ──────────────────────────────────────────────── */
async function boot(){
  try{
    const res=await fetch(`${API}/initial-data`);
    if(!(res.headers.get('content-type')||'').includes('application/json'))
      throw new Error(t('notJson').replace('{api}',API).replace('{flask}',FLASK));
    const d=await res.json();
    ids=d.product_ids||{};
    recipeInputs=d.recipe_inputs||{};
    schematics=d.schematics||{};
    typeVolumes=d.type_volumes||{};
    allConstellations=d.bases||[];
    allProducts=d.products||[];
    productTiers=d.product_tiers||{};
    regions=d.regions||{};
    systemCounts=d.system_counts||{};
    renderTierFilter(); renderProducts(); renderConstellations();
    dataCounts={bases:(d.bases||[]).length, products:(d.products||[]).length};
    renderDataChip();
    (d.data_problems||[]).forEach(t=>msg(t,true));

  }catch(e){ msg(e.message,true); dataCounts='failed'; renderDataChip(); }

  buildSortMenu();
}
function setSort(k){ sortBy=k; document.getElementById('sortMenu').classList.remove('open');
  buildSortMenu(); renderDash(); renderColonies(); }

/* ── Списки выбора ────────────────────────────────────────────── */
/* Кнопки-фильтры по тиру (P2/P3/P4) над списком продуктов (17.09.2026,
   Фаза 11, по прямому запросу пользователя) — сужают тот же allProducts,
   что и текстовый поиск, в ДОПОЛНЕНИЕ к нему, не вместо (пустой набор
   нажатых тиров — «все тиры», как и было раньше). Сами буквы P2/P3/P4 —
   термины EVE, не переводятся. */
function renderTierFilter(){
  const box=document.getElementById('tierFilter'); if(!box) return;
  box.innerHTML=PRODUCT_TIER_ORDER.map(tier=>
    `<button class="mini${activeTiers.has(tier)?' active':''}" onclick="toggleTierFilter('${tier}')">${tier}</button>`
  ).join('');
}

function toggleTierFilter(tier){
  if(activeTiers.has(tier)) activeTiers.delete(tier); else activeTiers.add(tier);
  renderTierFilter(); renderProducts();
}

function renderProducts(){
  const q=(document.getElementById('prodSearch').value||'').toLowerCase();
  const list=document.getElementById('productList');
  list.innerHTML=allProducts
    .filter(n=>(!activeTiers.size || activeTiers.has(productTiers[n])) && n.toLowerCase().includes(q))
    .map(n=>{
    const id=ids[n];
    return `<label><input type="checkbox" ${chosen.products.has(n)?'checked':''}
        onchange="togglePick('products','${n.replace(/'/g,"\\'")}',this.checked)">
      ${id?`<img src="${ICON(id)}" alt="">`:'<span style="width:20px"></span>'}
      <span>${n}</span></label>`;
  }).join('')||`<div style="padding:8px;color:var(--dim);font-size:12px">—</div>`;
  renderChosen('products','productChosen');
  const cnt=document.getElementById('prodCount');
  if(cnt) cnt.textContent=`${chosen.products.size} / ${allProducts.length}`;
  fitStepFills();
}

function renderConstellations(){
  const list=document.getElementById('constList');
  const names=Object.keys(regions);
  // Если в выгрузке есть колонка Region — группируем по регионам
  // и даём выбрать регион целиком одним щелчком. Если колонки нет,
  // показываем плоский список: выдумывать группировку не из чего.
  let html='';
  if(names.length){
    names.forEach(r=>{
      const inR=regions[r];
      const all=inR.every(c=>chosen.constellations.has(c));
      html+=`<div class="group-title">${r}
        <button class="mini" style="float:right;margin-top:-2px"
          onclick="pickRegion('${r.replace(/'/g,"\\'")}',${!all})">
          ${all?t('clearAll'):t('selectAll')}</button></div>`;
      html+=inR.map(c=>constRow(c)).join('');
    });
  } else {
    html=allConstellations.map(c=>constRow(c)).join('');
  }
  list.innerHTML=html||`<div style="padding:8px;color:var(--dim);font-size:12px">—</div>`;
  renderChosen('constellations','constChosen');
  const total=names.length?names.reduce((s,r)=>s+regions[r].length,0):allConstellations.length;
  const cnt=document.getElementById('constCount');
  if(cnt) cnt.textContent=`${chosen.constellations.size} / ${total}`;
  fitStepFills();
}

function constRow(c){
  const n=systemCounts[c];
  return `<label><input type="checkbox" ${chosen.constellations.has(c)?'checked':''}
      onchange="togglePick('constellations','${c.replace(/'/g,"\\'")}',this.checked)">
    <span>${c}</span>${n?`<span class="cnt">${n} ${t('systems')}</span>`:''}</label>`;
}

function renderChosen(key,boxId){
  const box=document.getElementById(boxId); if(!box) return;
  const items=[...chosen[key]];
  box.innerHTML=items.length
    ? items.map(v=>`<span>${v}<b onclick="togglePick('${key}','${v.replace(/'/g,"\\'")}',false)">×</b></span>`).join('')
    : `<span class="none">${t('nothingChosen')}</span>`;
}

function togglePick(key,value,on){
  on?chosen[key].add(value):chosen[key].delete(value);
  key==='products'?renderProducts():renderConstellations();
  if(key==='constellations') loadSystems();
  if(key==='products') loadAdvice();
}
function clearPick(key){ chosen[key].clear();
  key==='products'?renderProducts():renderConstellations();
  if(key==='constellations') loadSystems(); }
function pickAllConstellations(){
  allConstellations.forEach(c=>chosen.constellations.add(c));
  renderConstellations(); loadSystems();
}
function pickRegion(name,on){
  (regions[name]||[]).forEach(c=>on?chosen.constellations.add(c):chosen.constellations.delete(c));
  renderConstellations(); loadSystems();
}

/* «Покупать P1 на бирже» (18.09.2026, planner.py::PlanRequest.purchase_p1) —
   добывающие констелляции в этом режиме не нужны планировщику вовсе, но
   loadSystems() (ниже) заполняет «Домашнюю систему» ИЗ выбранных
   констелляций — без них список систем остался бы пустым. Поэтому при
   включении подставляются все констелляции региона той же
   pickAllConstellations(), что и у кнопки «Весь регион», а прежний
   выбор пользователя запоминается и возвращается при выключении, не
   теряется молча. */
let purchaseP1=false, savedConstellationsBeforePurchaseP1=null;
function togglePurchaseP1(checked){
  purchaseP1=checked;
  document.getElementById('mineSection').classList.toggle('disabled-section', checked);
  if(checked){
    savedConstellationsBeforePurchaseP1=picked('constellations');
    pickAllConstellations();
  } else {
    clearPick('constellations');
    (savedConstellationsBeforePurchaseP1||[]).forEach(c=>chosen.constellations.add(c));
    savedConstellationsBeforePurchaseP1=null;
    renderConstellations(); loadSystems();
  }
  // Подсказка «персонажей больше/меньше, чем нужно» считает добывающие
  // колонии по-разному в этих двух режимах (18.09.2026) — пересчитать
  // сразу, а не ждать, пока пользователь тронет список продуктов.
  loadAdvice();
}

function fill(id,items){ document.getElementById(id).innerHTML=items.map(v=>`<option value="${v}">${v}</option>`).join(''); }
function picked(id){ return chosen[id] ? [...chosen[id]] : []; }
function msg(t,bad){ document.getElementById('messages').insertAdjacentHTML('beforeend',
  `<div class="msg${bad?' bad':''}">${t}</div>`); }

async function loadSystems(){
  const c=picked('constellations');
  if(!c.length){ fill('factorySys',[]); return; }
  // Домашняя система (переработка) выбирается из ВСЕГО региона, не
  // только из констелляций, выбранных под добычу (21.09.2026, по
  // прямому запросу пользователя — логистика возит сырьё между
  // системами, привязывать переработку к добыче незачем); allConstellations
  // (boot(), из d.bases) — уже весь регион, отдельно собирать не нужно.
  // ccu — тем же способом, что и loadThresholds(): по нему сервер
  // подбирает домашнюю систему с наибольшим числом Barren/Temperate
  // планет маленького радиуса и налогом POCO 1% (см. recommend_home_
  // system(), domain/factory_site.py); без выбранных персонажей ccu=0,
  // сервер честно вернёт recommended=null — останется старое поведение.
  const ccu=Math.max(0,...crew.map(x=>x.ccu||0));
  const r=await fetch(`${API}/systems`,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({constellations:allConstellations, ccu})});
  const d=await r.json();
  const systems=d.systems||[];
  fill('factorySys',systems);
  if(d.recommended && systems.includes(d.recommended))
    document.getElementById('factorySys').value=d.recommended;
  loadSystemPlanets();
}

/* Единая точка POST /api/calculate: подставляет язык, чтобы предупреждения
   и допущения пришли на языке интерфейса. */
async function postCalculate(body){
  try{
    const r=await fetch(`${API}/calculate`,{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({...body, lang})});
    return await r.json();
  }catch(e){ msg(e.message,true); return null; }
}

/* Смена языка после построения плана: перезапрашиваем только тексты
   сервера (предупреждения, допущения, блок решения) — план не меняется. */
async function relocalizePlan(){
  if(!plan.length || !lastRequest) return;
  const d=await postCalculate(lastRequest);
  if(!d || d.status!=='success') return;
  lastPlanResp=d;
  lastWarnings=d.warnings||[]; lastAssumptions=d.assumptions||[];
  renderMessages(); renderDecision(d);
}

async function calculate(allowSingle, surplusMining){
  if(!picked('products').length){ msg(t('pickProducts'),true); return; }
  if(!purchaseP1 && !picked('constellations').length){ msg(t('pickConst'),true); return; }
  const body={constellations:picked('constellations'),
    factory_sys:document.getElementById('factorySys').value,
    target_products:picked('products'),
    extraction_margin:parseFloat(document.getElementById('margin').value)||1.0,
    allow_single_template_fallback:!!allowSingle,
    surplus_mining:!!surplusMining,
    // Флажок убран из интерфейса (15.09.2026) — сервер и так игнорирует
    // это поле (api/blueprints/plans.py форсирует False), но не шлём
    // выдуманное значение несуществующего чекбокса.
    direct_p2:false,
    purchase_p1:purchaseP1,
    lines_per_target:currentLinesPerTarget()};
  document.getElementById('messages').innerHTML='';
  document.getElementById('decision').innerHTML='';
  const d=await postCalculate(body);
  if(!d || d.status!=='success'){ if(d) msg(d.message,true); return; }

  plan=d.data||[]; staffing=d.staffing||{}; lastLogistics=d.logistics||null;
  lastRequest=body; currentPlanId=null; lastPlanResp=d;
  lastWarnings=d.warnings||[]; lastAssumptions=d.assumptions||[];
  renderMessages();
  renderDecision(d);
  renderAnswers(); renderTable(); renderDash(); renderCrew(); renderShopping(); renderProfit(); renderPocoProfit();
  // При упоре в размер планет остаёмся в настройках: выбор делать там,
  // а пустой дашборд ничего не объясняет.
  show(d.needs_user_decision ? 'setup' : 'dash');
}

function renderMessages(){
  const box=document.getElementById('messages');
  const crit=[...new Set((lastPlanResp&&lastPlanResp.critical_warnings)||[])];
  const critSet=new Set(crit);
  // Одно и то же предупреждение (например «Переработка P4: не хватило
  // свободных персонажей») сервер шлёт по разу на КАЖДУЮ непристроенную
  // колонию — «5 замечаний» превращалось в 2-3 разных текста, повторённых
  // подряд (18.09.2026, найдено пользователем по скриншоту). Список —
  // ради разнообразия проблем, не счётчика колоний; дедуп по тексту,
  // счётчик в заголовке — тоже по дедупнутому списку, не по сырому.
  const rest=[...new Set((lastWarnings||[]).filter(w=>!critSet.has(w)).concat(lastAssumptions||[]))];
  box.innerHTML=crit.map(w=>`<div class="msg bad">${esc(w)}</div>`).join('');
  if(rest.length) box.insertAdjacentHTML('beforeend',
    `<details class="msgs"><summary>${pl(rest.length,'pNote')} ${t('onPlan')}</summary>
     ${rest.map(w=>`<div class="msg">${esc(w)}</div>`).join('')}</details>`);
}

// Показываем ЗАЛОГИНЕННЫХ персонажей и их занятость по плану.
// Список приходит с сервера, поэтому переживает перезаход на сайт.
function renderCrew(){
  const box=document.getElementById('crew'); if(!box) return;
  // Кнопка входа в топбаре нужна, только пока НИ ОДИН персонаж этого
  // визита не вошёл через EVE SSO — дальше эту роль берёт на себя
  // «Добавить персонажа» в самой карточке (17.09.2026, Фаза 11, п.1).
  const hasLinked=crew.some(c=>c.esi_linked);
  const ssoBtn=document.getElementById('ssoBtn');
  if(ssoBtn) ssoBtn.style.display=hasLinked?'none':'';
  const hintBox=document.getElementById('crewHint');
  if(hintBox) hintBox.innerHTML=hasLinked
    ? `<button class="mini accent" onclick="startLogin()">${t('addCharacter')}</button>`
      // Кнопка видна, только если ТЕКУЩАЯ сессия прошла /api/admin/ping
      // (checkAdminAccess(), вызывается один раз при загрузке страницы) —
      // персонаж есть в PI_ADMIN_CHARACTER_IDS (28.09.2026, по прямому
      // запросу пользователя). Сам admin.html повторяет ту же проверку
      // на сервере — эта кнопка только для удобства навигации, не замена
      // серверной проверке доступа.
      + (isAdmin ? `<button class="mini accent" onclick="location.href='/admin.html'">${t('adminPanel')}</button>` : '')
    : '';
  if(!crew.length){
    box.innerHTML=`<div style="color:var(--dim);font-size:12px">${t('noData')}</div>`;
    fitStepFills();
    return;
  }
  // Ключ — имя персонажа: строка плана несёт character (имя), не id
  // (domain/planner.py PlanRow.to_dict его не отдаёт), а у crew — только
  // character_id (id нет вовсе). Раньше оба ключа были undefined, и все
  // персонажи делили один и тот же общий счётчик.
  const busy={};
  plan.forEach(r=>{
    const c=busy[r.character]=busy[r.character]||{mine:0,proc:0};
    isMining(r)?c.mine++:c.proc++;
  });
  // Группа «основной + альты» (docs/ROADMAP.md, Фаза 9, 16.09.2026) —
  // предлагается только для esi-персонажей этого визита (dev-заглушки
  // никогда не проходят SSO, group_characters() их и не тронет). Текущее
  // состояние — общий primary_character_id, если он ОДИН на всех
  // esi-персонажей визита; разнобой (после конфликта/ручной правки в БД)
  // — честно «не выбран», а не гадать, кто из них главнее.
  const linked=crew.filter(c=>c.esi_linked);
  const groupIds=new Set(linked.map(c=>c.primary_character_id).filter(x=>x!=null));
  const currentPrimary=groupIds.size===1?[...groupIds][0]:null;

  box.innerHTML=crew.map(c=>{
    const b=busy[c.name]||{mine:0,proc:0}, used=b.mine+b.proc;
    const spread=(lastLogistics&&lastLogistics.characters||[]).find(x=>x.character===c.name);
    const sys=spread?` · ${plural(spread.system_count,t('sysOne'),t('sysFew'),t('sysMany'))}`:'';
    // Основной группы — просто метка (группа уже сложилась сама при
    // входе, править тут нечего); у альтов вместо неё кнопка ручной
    // поправки, только если группа реально из 2+ человек (17.09.2026,
    // Фаза 11, п.3 — радиокнопки выбора упразднены как следствие
    // автогруппировки, не заменены новым способом выбора вручную).
    const isPrimary=c.esi_linked && c.character_id===currentPrimary;
    const primaryControl=(!c.esi_linked || linked.length<=1)?'':
      isPrimary?`<span class="mini" style="padding:1px 6px;font-size:10px;
          pointer-events:none;color:var(--amber);border-color:var(--amber)">${t('groupPrimary')}</span>`
      :`<button class="mini" style="padding:1px 6px;font-size:10px"
          title="${t('groupHint')}" onclick="makePrimary(${c.character_id})">${t('groupSave')}</button>`;
    return `<div title="CCU ${c.ccu} · IC ${c.ic} · ${c.planet_slots} ${t('planetSlots')}${
      spread?' · '+spread.systems.join(', '):''}">
      <div class="crew-primary-slot">${primaryControl}</div>
      <div class="pilot-lg" style="width:22px;height:22px;font-size:9px">${initials(c.name)}${avatarImg(c.character_id)}</div>
      <span>${esc(c.name)}</span>
      <span class="lv">${used}/${c.planet_slots}${sys}</span>
      ${c.needs_reconnect?`<button class="mini reconnect" style="padding:1px 6px;font-size:10px;margin-left:6px"
        title="${t('reconnectHint')}" onclick="startLogin()"
        data-i18n="reconnect">${t('reconnect')}</button>`:''}
      ${c.esi_linked?`<button class="mini" style="padding:1px 6px;font-size:10px;margin-left:6px"
        title="${t('unlinkHint')}" onclick="unlinkCharacter(${c.character_id})"
        data-i18n="unlink">${t('unlink')}</button>`:''}</div>`;
  }).join('');
  fitStepFills();
}

/* Постоянная группа «основной + альты» (docs/ROADMAP.md, Фаза 9,
   16.09.2026; автогруппировка при входе — Фаза 11, п.3, 17.09.2026):
   группа теперь собирается сама на сервере при входе (api/blueprints/
   auth.py::_store()) — первый персонаж визита становится основным
   сам себе, следующие подтягиваются автоматически. Эта кнопка — только
   ручная ПОПРАВКА: сделать конкретного альта основным, если автоматика
   выбрала не того. */
async function makePrimary(characterId){
  const r=await fetch(`${API}/auth/group`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({primary_character_id:characterId}),
  });
  const d=await r.json().catch(()=>({}));
  if(!r.ok){ msg(d.message||t('groupSaveFail'), true); return; }
  msg(t('groupSaved'));
  await loadCrew();
}

/* Карточки 01/03 «Что производим»/«Где перерабатываем» должны быть
   той же высоты, что и 02 «Где добываем» (самая длинная — там список
   созвездий), и заполнять её вплоть до низа списком продуктов/
   персонажей, а не оставлять пустоту снизу. Через чистый CSS
   (align-items:stretch + flex:1 внутри) это не сделать напрямую:
   .picker-list/.crew — прокручиваемые области, и без max-height их
   «естественная» (нескроллящаяся) высота участвует в расчёте высоты
   строки грида — при 53 продуктах без пролистывания строка раздувалась
   до ~1800px (проверено). Поэтому max-height у них остаётся в CSS как
   безопасное значение по умолчанию, а здесь после отрисовки высота
   карточек измеряется по факту и задаётся явно style.height — тогда
   у .picker-list/.crew появляется предок с определённой (не auto)
   высотой, и flex:1 у них можно безопасно освободить от max-height:
   раскручивать больше нечего, снизу их ограничивает уже сама карточка. */
function fitStepFills(){
  const cards=document.querySelectorAll('.steps-grid > .step-card');
  if(cards.length<3) return;
  const list=document.getElementById('productList');
  const crewBox=document.getElementById('crew');
  cards.forEach(c=>{ c.style.height=''; });
  if(list) list.style.maxHeight='';
  if(crewBox) crewBox.style.maxHeight='';

  // Узкий экран — карточки уже не в одну строку (auto-fit сам перенёс
  // их в столбец), выравнивать по высоте нечего.
  const tops=Array.from(cards).map(c=>c.offsetTop);
  if(!tops.every(top=>top===tops[0])) return;

  const target=Math.max(...Array.from(cards).map(c=>c.getBoundingClientRect().height));
  cards.forEach(c=>{ c.style.height=target+'px'; });
  if(list) list.style.maxHeight='none';
  if(crewBox) crewBox.style.maxHeight='none';
}
window.addEventListener('resize',fitStepFills);

function renderAnswers(){
  const mining=plan.filter(isMining);
  const worst=plan.reduce((w,r)=>Math.max(r.cpu_percent,r.pg_percent)>Math.max(w.cpu_percent||0,w.pg_percent||0)?r:w,{});
  const short=Object.values(staffing).reduce((n,s)=>n+(s.characters_needed||0),0);
  const cards=[
    {label:t('mColonies'),value:plan.length,
     note:`${mining.length} ${t('mining')} · ${plan.length-mining.length} ${t('processing')}`},
    {label:t('mChars'),value:new Set(plan.map(r=>r.char_id)).size,
     note:`${t('onPlanets')} ${pl(new Set(plan.map(r=>r.system+r.planet)).size,'pPlanet')}`},
    {label:t('mPeak'),value:worst.pg_percent?Math.max(worst.cpu_percent,worst.pg_percent).toFixed(0):'—',
     unit:'%',warn:Math.max(worst.cpu_percent||0,worst.pg_percent||0)>=90,
     bad:Math.max(worst.cpu_percent||0,worst.pg_percent||0)>=95,
     note:worst.system?`${worst.system} ${romanNumeral(worst.planet)}`:t('noData')},
    {label:t('mSpread'),
     value:(lastLogistics&&lastLogistics.avg_systems_per_character)||'—',
     note:lastLogistics
        ? `${t('logi')} · ${plural(lastLogistics.systems_total,t('sysOne'),t('sysFew'),t('sysMany'))}`
        : '',
     good:lastLogistics&&lastLogistics.avg_systems_per_character<=1.5,
     warn:lastLogistics&&lastLogistics.avg_systems_per_character>2.5},
    {label:t('mShort'),value:short||'0',bad:short>0,good:!short,accent:short>0,
     note:short?Object.values(staffing).map(s=>`${t('role_'+(s.role_key||'proc'))}: CCU ${s.min_ccu_level}+`).join(' · '):t('planClosed')}
  ];
  document.getElementById('answers').innerHTML=cards.map(c=>`
    <div class="answer${c.accent?' accent':''}"><div class="label">${c.label}</div>
      <div class="value${c.bad?' bad':c.warn?' warn':c.good?' good':''}">${c.value}${c.unit?`<span class="unit-s">${c.unit}</span>`:''}</div>
      <div class="note">${c.note}</div></div>`).join('');
}

function renderTable(){
  if(!plan.length){ document.getElementById('tableWrap').innerHTML=`<div class="empty">${t('planEmpty')}</div>`; return; }
  const rows=[...plan].sort((a,b)=>Math.max(b.cpu_percent,b.pg_percent)-Math.max(a.cpu_percent,a.pg_percent));
  // Лучшая (наименьшая) ставка POCO среди перерабатывающих строк плана —
  // чтобы пометить те, что вынужденно ушли на планету с более высоким
  // налогом (17.09.2026, по прямому запросу пользователя; сама
  // приоритизация — domain/factory_site.py). Та же логика, что уже
  // используется для подсветки CPU/PG>=90 чуть ниже — простое сравнение
  // на фронтенде, без отдельного серверного флага на каждой строке.
  const procRates=plan.filter(r=>r.role_key==='proc' && r.poco_rate!=null).map(r=>r.poco_rate);
  const bestProcRate=procRates.length?Math.min(...procRates):null;
  const body=rows.map((r,i)=>{
    const peak=Math.max(r.cpu_percent,r.pg_percent), reserve=100-peak, pgb=r.pg_breakdown||{};
    const pocoOutlier=r.role_key==='proc' && r.poco_rate!=null && bestProcRate!=null && r.poco_rate>bestProcRate;
    const pocoCell=r.poco_rate!=null
      ? `<span class="pct${pocoOutlier?' warm':''}" title="${pocoOutlier?t('pocoHigherThanBest'):''}">${(r.poco_rate*100).toFixed(1)}</span>`
      : `<span class="pct" style="color:var(--dim)">${t('noData')}</span>`;
    return `<tr>
      <td class="idx num">${i+1}</td>
      <td><div style="color:var(--bright)">${r.character}</div>
          <div style="margin-top:2px"><span class="rolebadge ${roleClass(r)}">${roleLabel(r)}</span></div></td>
      <td><div class="num">${r.system} ${romanNumeral(r.planet)}</div>
          <div class="parts" style="margin:0">${r.planet_type} · ${nloc(Math.round(r.planet_radius_km))} ${t('km')}</div></td>
      <td><span style="color:var(--read)">${r.res_out}</span>
          <div class="parts" style="margin:0">${factoryLabel(r)}${r.res_in?' ← '+r.res_in:''}</div></td>
      <td><div class="stack"><i class="s-str" style="width:${pgb.structures||0}%"></i>
          <i class="s-hed" style="width:${pgb.heads||0}%"></i>
          <i class="s-lnk" style="width:${pgb.links||0}%"></i></div>
          <div class="parts">${t('sStruct')} ${pgb.structures||0} · ${t('sHeads')} ${pgb.heads||0} · ${t('sLinks')} ${pgb.links||0}</div></td>
      <td class="r"><span class="pct ${r.cpu_percent>=90?'hot':r.cpu_percent>=75?'warm':''}">${r.cpu_percent.toFixed(1)}</span></td>
      <td class="r"><span class="pct ${r.pg_percent>=90?'hot':r.pg_percent>=75?'warm':''}">${r.pg_percent.toFixed(1)}</span></td>
      <td class="r"><span class="pct" style="color:${reserve<5?'var(--alarm)':reserve<15?'var(--amber)':'var(--calm)'}">${reserve.toFixed(1)}</span></td>
      <td class="r">${pocoCell}</td>
    </tr>`;}).join('');
  document.getElementById('tableWrap').innerHTML=`<table>
    <thead>
      <tr class="group"><th colspan="3">${t('thColony')}<small>${t('thColonySub')}</small></th>
        <th>${t('thProd')}<small>${t('thProdSub')}</small></th>
        <th colspan="5">${t('thLoad')}<small>${t('thLoadSub')}</small></th></tr>
      <tr class="cols"><th>#</th><th>${t('thChar')}</th><th>${t('thPlanet')}</th><th>${t('thProduct')}</th>
        <th>${t('thPgFrom')}</th><th class="r">CPU %</th><th class="r">PG %</th><th class="r">${t('thReserve')}</th>
        <th class="r">POCO %</th></tr>
    </thead><tbody>${body}</tbody></table>`;
}

/* Прогрессивное скрытие чипов-индикаторов и подписи бренда — вместо
   набора фиксированных @media-порогов (были: сначала подобраны на
   глаз, потом расходились с реальной шириной заново при каждой мелочи
   — число игроков ESI переменной длины, EN-перевод короче/длиннее RU,
   а Saira Condensed с прописными буквами Фазы 8 сделал вкладки и кнопки
   шире прежнего шрифта). Здесь перенос проверяется по факту (сравнение
   top прямых детей .topbar — flex-wrap:wrap остаётся включённым как
   защита на случай, если спрятать больше нечего, а не основной
   механизм) и лечится, а не предугадывается по ширине экрана. Порядок
   жертв: сначала данные, потом сборщики и версия, потом ESI, потом
   подпись бренда — и только если этого всё ещё не хватило (очень узкая
   и при этом длинная панель), кнопки Справка/О проекте и переключатель
   темы: их не прячем безвозвратно, а физически переносим внутрь
   #infoChips, чтобы попасть в тот же выпадающий список «Ещё» (см.
   #moreBtn ниже) — иначе они были бы недоступны совсем, а этого правило
   проекта не допускает. На совсем узких мобильных ширинах того же
   недостаточно даже с переключателем темы — тогда туда же уходит и
   переключатель языка (тоже не теряется, просто раскрывается через
   «Ещё»). Вход через EVE SSO и вкладки не прячутся никогда — это либо
   основной сценарий входа, либо структурная навигация. */
const TOPBAR_HIDE_PRIORITY = ['chipData','chipJobs','chipVer','chipEsi','aboutBtn','helpBtn','themeBtn','langGroup'];
/* Элементы, которые при скрытии физически переносятся в #infoChips.
   Порядок здесь — порядок восстановления (не порядок скрытия): каждый
   вставляется перед своим якорем, поэтому чтобы порядок в .right совпал
   с исходной разметкой (Справка, О проекте, язык, тема, SSO), сначала
   возвращаются на место более правые элементы (язык, тема — оба перед
   SSO), и только потом Справка/О проекте (перед уже вернувшимся языком). */
const TOPBAR_OVERFLOW = [
  {id:'langGroup', anchor:'#ssoBtn'},
  {id:'themeBtn', anchor:'#ssoBtn'},
  {id:'helpBtn', anchor:'#langGroup'},
  {id:'aboutBtn', anchor:'#langGroup'},
];

/* Сравнение top у прямых детей .topbar ложно срабатывало на пустых
   (уже спрятанных) элементах нулевой высоты и было неустойчиво ровно
   на границе, где контент помещается впритык — при round-trip той же
   ширины иногда давало то true, то false. scrollWidth с временным
   flex-wrap:nowrap не зависит ни от того, ни от другого: это прямой
   вопрос «влезает ли контент в одну строку», а не косвенный вывод по
   положению элементов после реального переноса. */
function topbarWrapped(){
  const bar=document.querySelector('.topbar'); if(!bar) return false;
  const prevWrap=bar.style.flexWrap;
  bar.style.flexWrap='nowrap';
  const overflow=bar.scrollWidth>bar.clientWidth;
  bar.style.flexWrap=prevWrap;
  return overflow;
}

function fitTopbar(){
  const subtitle=document.querySelector('.brand small');
  const infoChips=document.getElementById('infoChips');
  const rightGroup=document.querySelector('.topbar .right');

  TOPBAR_HIDE_PRIORITY.forEach(id=>document.getElementById(id)?.classList.remove('js-hidden'));
  subtitle?.classList.remove('js-hidden');
  TOPBAR_OVERFLOW.forEach(({id, anchor})=>{
    const el=document.getElementById(id);
    const anchorEl=rightGroup?.querySelector(anchor);
    if(el && anchorEl) rightGroup.insertBefore(el, anchorEl);
  });

  const overflowIds=TOPBAR_OVERFLOW.map(o=>o.id);
  for(const id of TOPBAR_HIDE_PRIORITY){
    if(!topbarWrapped()) break;
    const el=document.getElementById(id);
    if(!el) continue;
    el.classList.add('js-hidden');
    if(overflowIds.includes(id)) infoChips?.appendChild(el);
  }
  if(topbarWrapped()) subtitle?.classList.add('js-hidden');

  const anyHidden=[...TOPBAR_HIDE_PRIORITY, 'brandSub'].some(id=>
    id==='brandSub' ? subtitle?.classList.contains('js-hidden') : document.getElementById(id)?.classList.contains('js-hidden'));
  document.querySelector('.topbar')?.classList.toggle('has-hidden', !!anyHidden);
}

/* Высота шапки меняется при переносе на вторую строку, поэтому отступ
   для прилипающей панели вида измеряется, а не задаётся числом. */
function measureTopbar(){
  const bar=document.querySelector('.topbar'); if(!bar) return;
  fitTopbar();
  document.documentElement.style.setProperty('--topbar-h', bar.offsetHeight+'px');
}
if(window.ResizeObserver){
  new ResizeObserver(measureTopbar).observe(document.querySelector('.topbar'));
} else {
  window.addEventListener('resize', measureTopbar);
}

/* Та же история у панели вида (.tools, тоже sticky, кнопки переносятся
   на вторую строку на узком экране) — без измерения scroll-margin-top у
   #colonies/#shopping (см. CSS) был бы занижен, и jumpTo() докручивал
   ровно до заголовка блока, а сам заголовок прятался под обеими липкими
   панелями (найдено пользователем 12.09.2026: «проматывает чуть ниже
   чем нужно»). */
function measureTools(){
  const bar=document.querySelector('#page-dash .tools'); if(!bar) return;
  document.documentElement.style.setProperty('--tools-h', bar.offsetHeight+'px');
}
if(window.ResizeObserver){
  new ResizeObserver(measureTools).observe(document.querySelector('#page-dash .tools'));
} else {
  window.addEventListener('resize', measureTools);
}

/* ── Вкладки и вид ──────────────────────────────────────────── */
function show(p){
  document.querySelectorAll('.page').forEach(x=>x.classList.toggle('on',x.id==='page-'+p));
  document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('on',x.dataset.page===p));
  // Пока страница «Настройки» скрыта (display:none), карточки нулевой
  // высоты — подогнать высоту можно только после того, как страница
  // стала видимой.
  if(p==='setup') fitStepFills();
}

/* После входа через EVE SSO callback (api/blueprints/auth.py::callback())
   редиректит на /?auth=ok или /?auth=error&reason=... — раньше фронтенд
   этот параметр вообще не читал, и добавление второго и следующих
   персонажей молча оставляло пользователя на дефолтной вкладке
   «Дашборд» (21.09.2026, по прямому запросу пользователя). auth=error
   раньше терялся точно так же — ошибка есть, а сказать о ней было
   некому: тот же пробел, чинится тем же изменением. history.replaceState
   — чтобы обновление страницы (F5) не повторяло переключение вкладки
   при уже отработанном редиректе. */
function handleAuthRedirect(){
  const params=new URLSearchParams(location.search);
  const auth=params.get('auth');
  if(!auth) return;
  show('setup');
  if(auth==='error') msg(params.get('reason')||t('ssoUnavailable'), true);
  history.replaceState(null,'',location.pathname);
}
document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>show(t.dataset.page));
document.querySelectorAll('.tool[data-view]').forEach(t=>t.onclick=()=>{
  view=t.dataset.view;
  document.querySelectorAll('.tool[data-view]').forEach(x=>x.classList.toggle('on',x===t));
  renderDash(); renderColonies();
});
document.addEventListener('click',e=>{ if(!e.target.closest('#sortMenu'))
  document.getElementById('sortMenu').classList.remove('open'); });

/* «Ещё» в шапке — те же чипы-индикаторы (ESI/сборщики/данные/версия),
   которые на мобильной ширине скрыты по одному (см. @media выше),
   выпадающим списком по клику. Один DOM-узел, один рендер
   (renderMeta/renderJobsChip/renderDataChip) — список просто открывает
   доступ к уже существующим элементам, не дублирует их. */
function toggleMoreMenu(){ document.getElementById('infoChips').classList.toggle('open'); }
document.addEventListener('click',e=>{ if(!e.target.closest('#infoChips') && !e.target.closest('#moreBtn'))
  document.getElementById('infoChips').classList.remove('open'); });

document.querySelectorAll('.lang button').forEach(b=>b.onclick=()=>{ lang=b.dataset.lang; applyLang(); });
document.addEventListener('keydown',e=>{ if(e.key==='Escape')closeModal(); });

// Язык и тема — предпочтения отображения, они принадлежат браузеру.
setTheme(store.get('pi-theme','dark'));
lang=store.get('pi-lang','ru');

measureTopbar(); measureTools();
handleAuthRedirect();
boot(); loadMeta(); loadCrew(); checkAdminAccess(); loadProfit(); loadColonies(); loadSaved(); applyLang();
// Онлайн меняется медленно; снимок обновляет фоновый сборщик,
// поэтому опрашиваем раз в минуту и только читаем готовое.
setInterval(loadMeta, 60000);
