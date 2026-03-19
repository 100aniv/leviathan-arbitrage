프로젝트 현황 및 전략별 분석
현재 단계와 단계별 성과

Leviathan 엔진은 현재 **Phase G (전략 수익성 복원)**에 있으며, Shadow 모드에서 전략별 검증을 수행한 이후 Optuna 기반의 파라미터 재튜닝(Phase H)을 준비하고 있다. 아키텍처는 도메인 모델(core), 전략(strategies), 실행(execution), 인프라(infra), 위험 관리(risk), 슬리피지 모델(friction) 등으로 분리된 헥사고날 구조를 채택하고 있어 이전 프로젝트에 비해 모듈화가 뛰어나고 일관된 책임 분리가 이루어져 있다.

최근 검증 결과

engine/config/strategy_activation.json에는 2026‑03‑15에 수행된 Shadow 검증 결과가 기록되어 있다. 이번 실행에서는 각 전략을 5초씩 단독으로 돌린 뒤 active_strategies와 disabled_strategies를 출력하도록 설계되었으나, 구현상의 mock key 버그 때문에 대부분의 전략이 0 트레이드로 처리되어 disabled_strategies 목록에 7개 전략이 모두 들어갔다. 즉, 전략이 충분한 트레이드를 생성하기 전에 검증을 종료한 탓에 “insufficient_data”로 분류됐다.

strategy_params.json에 따르면 8개의 전략(spot_futures, funding_rate, triangular, cex_dex, cross_exchange, futures_futures, statistical_arb, latency_arb)이 존재하며, spot_futures·funding_rate·cross_exchange는 status="READY", 나머지 전략은 MONITOR로 표시돼 있다. 이는 파라미터 튜닝 결과(WFE ≥ 1 또는 ≥ 0.6)에 따라 실행 준비가 된 전략을 구분한 것이다.

US‑068 계획서에서는 WFE가 높은 3개의 전략만 Optuna 재튜닝 대상으로 삼고 나머지는 MONITOR 상태로 유지하며, latency_arb는 아직 튜닝 파이프라인에 포함되지 않았다고 명시한다.

결론적으로 현재 활성화된 전략은 5개가 아니라 READY 상태의 3개뿐이며, 나머지는 모니터링 중이다. 사용자께서 “5개 전략만 활성화”라고 느끼는 이유는 strategy_params.json에 5개 이상 목록이 있지만 실제로는 3개만 READY이기 때문이다. Shadow 검증 코드의 mock 버그로 인해 strategy_activation.json에는 모든 전략이 비활성화로 표시됐으므로, 이 파일만 보고 전략 수를 판단해서는 안 된다.

평가 기준과 문서
현재 구현된 평가 로직

StrategyValidationOrchestrator는 각 전략을 10분(테스트 버전에서는 5초)씩 개별적으로 실행하여 총 수익(PnL)과 트레이드 수가 최소값(min_trades_threshold)을 넘는지만 확인한다. PnL이 양수이면 “profitable”, 음수이면 “unprofitable”, 트레이드 수가 적으면 “insufficient_data”로 분류하는 단순 로직이다.

요구되는 정량적 지표

프로젝트의 QUANT_MANIFESTO.md와 docs/strategies_ko.md는 보다 엄격한 평가 기준을 제시한다:

Walk‑Forward Analysis / Live Gate – 샤프비율 ≥ 2.5, 최대 드로다운 < 5%, 하루 신호수 ≥ 100 등을 통과해야 실거래로 진입한다.

Beta Gate – 순손익 > 0, Profit Factor > 1.2, 최대 드로다운 < 2%가 최소 조건이다.

또한 연구 보고서에서는 전략별로 동적 임계치, OU 프로세스 기반 절반 시간(half‑life) 필터, Kalman 필터 기반 해지 비율, Bellman‑Ford 사이클 탐지 등 고급 기법을 적극적으로 도입할 것을 권장한다.

현행 StrategyValidationOrchestrator가 단순 PnL과 트레이드 수만으로 전략을 분류하는 것은 이러한 높은 기준과 명백히 상충한다. 따라서 평가 로직을 강화하여 샤프비율, 드로다운, Profit Factor, half‑life 등 복수의 지표를 사용하도록 개선해야 한다.

전략별 분석 및 퀄리티 평가
1. Spot–Futures Basis Arbitrage (spot_futures)

구현 현황 – 가격 괴리(basis)가 min_basis_bps보다 클 때 진입하고, 펀딩 레이트가 지정 임계치보다 낮을 경우 포지션을 회피한다. 청산은 basis 가 일정 값 이하로 축소될 때 또는 stop_loss_pct에 도달할 때 수행한다. 파라미터는 자동 튜닝 결과(WFE = 1.52)로 설정돼 있으며 entry/exit threshold가 수치로만 지정되어 있다.

연구 및 기관 수준과의 비교 – 연구 보고서는 **동적 basis 임계치(롤링 백분위수 기반)**와 펀딩 레이트 예측(LSTM 모델), 정치 이벤트 캘린더를 사용한 변동성 조정 등을 권장한다. 현재 구현은 정적 임계치에만 의존하므로, 변동성이 큰 암호화폐 시장에서 거짓 신호를 걸러내지 못하고 수익성이 낮다. 또한 펀딩 레이트가 단순 필터로만 사용되며 롱·숏 포지션을 적절히 교차하지 않는다. 기관급 전략에서는 지정가 주문 및 시장 심도 고려, 만기까지의 잔여 기간을 기반으로 한 동태적 포지션 보정 등을 실시한다.

완성도 평가 – 기초적인 전략으로는 적절하지만 우수한 지표 검증이나 동적 모델링이 없어 기관 수준의 퀄리티에 미치지 못한다. 연구 보고서의 개선 제안(동적 임계치, LSTM 기반 펀딩 예측)을 반영하면 경쟁력이 향상될 것이다.

2. Funding Rate Arbitrage (funding_rate)

구현 현황 – 두 거래소 간 펀딩 레이트 차가 min_funding_diff_bps 이상일 때 롱/숏 포지션을 잡아 funding 수익을 얻는다. 포지션 진입은 settlement window를 고려하여 분기하며, 동일 쌍에 중복 포지션을 방지한다.

연구 및 기관 수준과의 비교 – 연구에서는 펀딩 레이트 예측(OU 프로세스 기반), Hyperliquid DEX와 CEX 간 차이를 이용한 다중 거래소 스캐너, settlement 타이밍 최적화 등을 제시한다. 현재 구현은 정적 임계치와 단일 settlement 타이밍(즉시 진입/청산)에 의존하므로 수익률이 제한적이다.

완성도 평가 – 기본적인 펀딩 수익 기회는 포착하지만 예측 모델·다중 거래소 모니터링·동적 자본 배분이 부족하여 전문 기관 전략과 격차가 크다. 개선을 통해 연 10%~20% 이상의 안정적인 APY를 달성할 수 있을 것으로 보인다.

3. Triangular Arbitrage (triangular)

구현 현황 – 세 통화/거래쌍의 환율을 곱해 이론적으로 1보다 큰 경우에 진입한다. min_profit_bps 이상일 때만 신호를 발동하며, 예치금 한도와 슬리피지까지 고려해 net profit이 양수일 때 주문을 제출한다.

연구 및 기관 수준과의 비교 – 최신 연구에서는 Bellman‑Ford 알고리즘이나 **Graph Neural Network(GNN)**를 사용하여 음의 사이클을 탐지하고, 주문서 깊이·라운드트립 지연·3 leg 모두의 슬리피지를 통합하여 실질 수익을 계산해야 한다고 강조한다. 현재 구현은 단순한 수학적 곱셈과 정적 임계치만 사용하며 세 leg 호출의 지연 한도(500ms)를 엄격히 준수하지 않는다.

완성도 평가 – 기존 전략은 소규모 자본으로는 사용 가능하지만, 대형 주문을 다룰 때는 주문서 깊이에 따른 슬리피지·네트워크 지연·거래소 응답 시간 등을 고려해야 한다. 연구 보고서에서 제시한 Bellman‑Ford 알고리즘 도입과 latency budget 설정은 필수적인 개선사항이다.

4. Cross‑Exchange Arbitrage (cross_exchange)

구현 현황 – 두 CEX 간 호가 스프레드가 min_spread_bps 이상일 때 진입하며, 주문 집행 전 book_depth를 확인하고 슬리피지 모델을 통해 예상 비용을 계산한 후 예상 순수익이 양수일 때만 실행한다. 환경 변수로 min/max 스프레드, 주문 수량, 거래쌍 등을 조정할 수 있는 유연성이 있다.

연구 및 기관 수준과의 비교 – 최신 논문에서는 ML 기반 스프레드 예측, 다중 경로 자금 전송(XRP·USDT Arbitrum), 선행 포지션을 위한 inventory management 등의 기법을 활용한다. 또한 트랜스퍼 지연과 김치 프리미엄처럼 지역적 요인을 고려한 다국적 전략이 중요해지고 있다. 현재 구현은 호가 차이만 보고 즉시 진입·청산하므로 오차를 줄이려면 사전 자금 배치와 스프레드 예측이 필요하다.

완성도 평가 – 슬리피지와 비용 계산이 포함돼 단순한 고스프레드 엔진보다는 한 단계 발전했지만, 예측 기반 진입·다중 자금 경로·동적 포지션 조정 등이 없어 기관 수준보다는 한참 미흡하다.

5. Futures–Futures Arbitrage (futures_futures)

구현 현황 – 두 거래소에서 같은 종목의 perpetual futures 가격 차이가 min_spread_bps 이상이면 진입한다. book_depth를 확인하고 max_position_size_usdt 제한 안에서 포지션을 잡는다. Funding rate 차를 고려하지 않고 단순 가격 차이에만 의존한다.

연구 및 기관 수준과의 비교 – 연구에서는 funding rate convergence trading(한 거래소에서 funding을 받아 다른 거래소에서 지급하는 구조), altcoin 집중, 스테일 데이터 탐지 등을 강조한다. 현재 구현은 단일 시간 틱에만 의존하므로 Phantom oppurtunity(체결 불가능한 기회)에 쉽게 속을 수 있으며, funding rate 수익을 놓친다.

완성도 평가 – 기본적인 구현으로 출발했지만, stale 데이터 검증·funding rate 차 수익·하프 라이프 기반 유지/청산을 도입해야 기관급 전략과 경쟁할 수 있다.

6. Statistical Arbitrage (statistical_arb)

구현 현황 – 가장 진보된 전략이다. Kalman 필터로 동적 헤지 비율을 추정, Z‑score 분포에 따른 진입/청산, OU half‑life 필터, 코인티그레이션 재검정, regime detection까지 포함한다. Cross‑asset(예: BTC–ETH, ETH–SOL)와 cross‑exchange 두 모드를 지원하며 거래 쌍별로 상태를 유지한다.

연구 및 기관 수준과의 비교 – 연구 보고서가 권장하는 Kalman 필터 + OU half‑life 조합, rolling cointegration test, HMM 기반 regime gate 등을 대부분 반영하고 있어 다른 전략에 비해 상당히 성숙하다. 다만 아직 **거래 대상 쌍 확장(ETH–SOL, BTC–BNB)**과 거래비용 기반 Z‑score 조정 등이 미흡하다.

완성도 평가 – 거의 기관급에 근접한 알고리즘적 정교함을 보인다. 구현의 정확성을 검증할 충분한 백테스트와 실전 데이터를 확보한다면, 다른 전략과 달리 즉시 유의미한 수익을 기대할 수 있다.

7. CEX–DEX Hybrid Arbitrage (cex_dex)

구현 현황 – CEX(주문서 기반)와 DEX(AMM) 간 가격 차이를 포착한다. 슬리피지 및 가스비를 고려해 순이익이 양수일 때만 주문을 전송하며, constant‑product AMM 모델을 이용해 DEX 슬리피지를 예측한다.

연구 및 기관 수준과의 비교 – MEV 분야의 최신 연구에 따르면 Flashbots Protect·MEV Blocker 등 프런트러닝 방어, 레이어 2 가스비 최적화, Solana/Jupiter 등 새로운 체인 통합이 CEX–DEX arb의 성패를 좌우한다. 또한 CEX와 DEX 간 전송을 줄이기 위해 다중 RPC 엔드포인트와 사전 자금 배치가 중요하다. 현 구현은 단일 체인/거래쌍, 고정 가스비에만 의존하므로 수익성이 제한된다.

완성도 평가 – 기본적인 AMM vs orderbook 차익을 포착하는 수준이다. 가스비 변동, MEV 리스크, 다중 체인 지원을 고려하면 아직 연구·개발이 많이 필요하다.

8. Latency Arbitrage (latency_arb)

구현 현황 – latency_arb.py는 CrossExchangeStrategy의 래퍼로서 latency_boost=True 옵션만 켜는 간이 구현이다. US‑068 계획서에는 아직 신호 생성기와 튜닝 파이프라인이 누락된 GAP으로 지적돼 있다.

연구 및 기관 수준과의 비교 – 마이크로초 단위의 네트워크 지연을 이용해 가격 차를 포착하는 전략이며, 전문 기관은 하드웨어 최적화·공동 위치화(co‑location)·특수 네트워크 경로를 사용한다. 현재 구현은 단순 래퍼 수준이므로 실제 시장에서 의미 있는 알파를 창출하기 어렵다.

완성도 평가 – 미구현에 가까워 추후 개발이 필요하다.

프로그램 전체 평가 및 이전 프로젝트와의 비교

아키텍처·코드 품질 – 새 엔진은 헥사고날 구조를 채택하고, Pydantic 모델과 타입 힌트를 통해 코드 품질을 향상시켰다. Shadow 모드와 Risk Guardian가 분리되어 있어 전략 실행과 위험 관리가 독립적으로 진화할 수 있다. 또한 make test/lint/format 등 CI 타깃이 제공되고, Prometheus/Grafana 연동으로 운영 가시성이 높다. 이러한 구조는 이전 arbitrage 프로젝트에서 지적된 모듈 중복과 cohesion 부재를 크게 개선했다.

테스트와 코드 리뷰 – US‑067_REVIEW.md에서 전략 검증 오케스트레이터는 대부분 요구 사항을 만족했지만, mock key 버그로 인해 모든 전략이 insufficient_data로 분류되는 문제가 발생했다. 이는 테스트의 품질이 실행 결과에 결정적인 영향을 미칠 수 있음을 보여준다. 또한 환경 변수 파싱 오류, Prometheus 메트릭 초기화 누락 등 하위 수준의 결함이 발견되었다.

전략 포트폴리오 완성도 – 8개의 전략 중 statistical_arb를 제외하면 대부분이 정적 임계치와 간단한 조건문에 의존해 있어 수익 기여도가 제한적이다. 특히 latency arb와 cex_dex는 미완성에 가깝고, futures_futures와 funding_rate도 연구 제안과 비교하면 단순하다. 따라서 현재 엔진은 준연구 단계(베타) 수준으로 평가되며, 기관급 시스템과 비교했을 때 다수의 고급 기능이 결여되어 있다.

이전 프로젝트 대비 개선점 – 사용자께서 언급한 과거 “arbitrage” 폴더는 코드 재사용성과 모듈화가 미흡했고 여러 참가자 간 기여가 응집되지 않았던 것으로 보인다. 현재의 Leviathan 엔진은 **명확한 폴더 구조, 문서화된 요구 사항, 단계별 계획(US‑066~068)**를 통해 프로젝트의 방향성을 정립했다. 다만 아직 개발 과정 중에 있어 최적화 및 고급 기능 구현이 필요한 상태다.

결론 및 권장 사항

전략 검증 로직 보강 – 단순 PnL/트레이드 수 대신 샤프비율, 최대 드로다운, Profit Factor, 거래비용까지 포함한 지표를 적용하고, statistical_arb에서 사용한 Kalman + OU half‑life 필터를 다른 전략에도 도입해야 한다.

고급 예측 모델 통합 – 펀딩 레이트와 스프레드 예측에 OU/LSTM 모델을 사용하고, 연구 보고서에서 제시한 동적 임계치를 적용하여 변동성에 적응하는 전략으로 발전시켜야 한다.

실시간 위험 관리 강화 – Stale 데이터 탐지, 네트워크 지연 모니터링, MEV 리스크 방어 등 실거래 환경에서 필요한 안전장치를 추가해야 한다.

전략 포트폴리오 균형 조정 – 현재 spot_futures, funding_rate, cross_exchange가 준비된 상태이나 수익 모델이 제한적이다. 가장 수익성이 높은 statistical_arb를 주축 전략으로 삼고, latency arb와 cex_dex는 추가 연구 후 도입하는 것이 바람직하다.

이전 프로젝트와의 시너지 – 과거 코드의 장점을 분석하여 재사용 가능한 모듈(예: Slack 알림, 배치 실행 스크립트 등)이 있다면 새로운 엔진에 통합하되, 중복과 복잡도를 줄이기 위해 헥사고날 구조를 유지해야 한다.

요약하면, Leviathan 엔진은 구조적 측면에서 과거 프로젝트보다 크게 개선되었으며, statistical_arb를 제외한 전략들은 아직 연구 단계이다. 평가 기준을 강화하고 최신 연구에서 제시한 고급 기법을 적용한다면, 기관급 수준에 가까운 안정적이고 수익성 높은 포트폴리오를 구축할 수 있을 것이다.

보고서에서는 현재 프로젝트 단계, 전략 활성화 문제, 평가 기준 문서, 전략별 현황, 프로그램 전체 평가 및 이전 프로젝트와의 비교를 다루었습니다.
추가 분석이나 궁금한 점이 있으시면 언제든지 말씀해 주세요.

출처
근데 너 왜 예전 정보를 보지??? 지금 acitvity 보면 훨씬 진행이 많이 되었는데
왜 대충 문서 읽고 예전단계를 보는걸까
지금은 너가 말한 알파벳 Phase는 예전에 끝내고 지금 S14까지 수행했는데
그냥 대충 문서 보고 코드도 안본거같은데

현재 작업 단계가 늦어진 것처럼 보인 이유는 오래된 문서나 노트패드가 아닌 최신 SSOT 문서를 확인하지 않은 데서 비롯된 오해입니다. SSOT.md는 LEVIATHAN 프로젝트의 유일한 설계 문서이며 마지막 업데이트는 2026‑03‑18입니다. 이 문서에 따르면 알파벳 Phase(A~M)와 S1~S12는 이미 완료됐고, 현재는 Phase S13과 S14를 모두 마친 후 TF QF 7차와 TF SF Stage 2를 통과한 상태입니다. 즉, 프로젝트는 이미 S14까지 진행됐으며 다음 단계는 TF SF Stage 3~6, TF Final, 그리고 라이브 출시입니다. SSOT가 제공하는 최신 테스트 결과는 4,843개 테스트가 모두 통과했고, 코드 커버리지는 86%이며, 두 시간짜리 Shadow 테스트에서 PnL +$3,312를 달성했습니다.

전략 활성화 현황과 평가 기준

SSOT의 전략 매트릭스에 따르면 7개 전략 중 두 개만 “활성(Active)” 상태입니다. cross_exchange와 futures_futures 전략이 활성화되어 있고, spot_futures·triangular·cex_dex는 시장 조건이나 회전 빈도 등의 이유로 “조건부(Conditional)” 대기 중이며, funding_rate와 statistical_arb는 “검증됨(Verified)” 단계입니다. latency_arb는 cross_exchange 전략으로 병합돼 더 이상 별도 전략으로 존재하지 않습니다. 이런 설정은 TF 준비 단계에서 손익이나 신호 수 등 복합 지표가 충분하지 않을 경우 전략 실행을 막기 위한 안전 장치입니다.

그동안 일부 테스트 스크립트나 계획서에서 단순 PnL/승률을 평가에 사용했으나, SSOT.md와 Quant Manifesto는 Shadow/TF 단계에서 **복합 지표(LiveGate 6‑check)**를 사용하는 것을 강조합니다. Stage 2 이상에서는 최대 손실률(MDD) < 5%, 하루 신호 수 ≥ 100, Kill Switch/Circuit Breaker/거래소 Health 상태, PnL ≥ 0, crash·loss_capped = 0 등을 모두 만족해야 다음 단계로 넘어갑니다. TF SF 단계에서는 이 기준에 더해 Sharpe ratio ≥ 2.0 및 전략별 승률 > 50%가 요구되고, TF Final에서는 Sharpe ratio ≥ 2.5가 추가됩니다. 따라서 단순 PnL이나 승률만으로 전략을 평가하는 것은 이미 금지돼 있으며, 진입/종료 여부는 위 복합 지표를 통과해야 합니다.

전략 코드 분석 및 완성도 평가

각 전략의 핵심 로직과 완성도를 코드를 통해 분석하면 다음과 같습니다.

전략	주요 기능/개선점	완성도 평가
cross_exchange	최대 ·최소 스프레드 bps와 최소 유동성(책깊이) 필터, 늦은 거래소 데이터를 걸러내는 anomaly guard, latency boost 모드(US‑194) 및 한국 거래소 필터, 거래량에 따른 포지션 제한 등이 구현되었습니다.	시장 안정성을 위한 다층 필터와 비용 계산을 갖춘 완성도 높은 전략. 기관급 수준은 아니지만 수수료·슬리피지·정합성 검사를 포함한 고급 기능이 있다.
futures_futures	cross_exchange와 유사한 구조에 선물 시장 전용 필터를 적용, 선물 거래소간 가격 차를 이용함. 단, dynamic threshold나 레짐 감지는 포함되지 않았다.	기본적인 스프레드 기반 전략으로 기능은 탄탄하지만, 시장 변동성에 대응하는 동적 임계값·레짐 분류가 부족해 상용 퀀트 시스템보다는 단순하다.
spot_futures	현물-선물 가격 차(basis)를 이용한 전략. 최소 basis와 adverse funding rate 임계값을 넘지 않으면 실행하지 않고, 두 다리가 동일한 거래소인지 확인하며 수익이 비용보다 크지 않으면 거래를 거부합니다.	매매 기본 구조는 잘 구현돼 있으나, 연구 보고서에서 제시된 OU half‑life·kalman 추정 등 고급 필터가 없어 현재 시장 상황에 따라 조건부로만 사용되고 있다.
funding_rate	펀딩 레이트 차이와 settlement window를 고려하여 일정 시간 전후에만 진입하며, 심볼별 중복 포지션을 방지하고 funding diff bps 임계값을 이용합니다.	포지션 중복 방지, settlement window 필터 등 기본 안정성 장치가 있지만, 장기 추세나 mean‑reversion을 고려한 동적 모델은 없다.
statistical_arb	Kalman 필터로 동적 hedge ratio를 추정하고 z-score 기반 진입/청산, adaptive threshold, 제로 교차 횟수, OU half‑life 최대값 제한 및 z-score hardstop 등이 적용돼 있습니다.	복잡한 통계 모델과 적응형 임계값을 사용해 가장 기관급에 가까운 전략. cross‑asset 모드와 cross‑exchange 모드를 모두 지원하며, Mean Reversion 오류 검출 등 고급 기능이 있다.
triangular, cex_dex	triangular는 세 거래쌍을 이용한 기본 차익거래이며, slippage와 fee를 감안해 최소 이익 조건만 검토합니다. cex_dex는 AMM의 x·y=k 슬리피지 모델과 가스비 추정, CEX/DEX 가격 차를 이용하는 전략으로, 수수료·가스비·마찰력을 모두 계산한 뒤 순수익이 충분할 때만 진입합니다.	triangular는 간단한 구조라 현 시장에서는 신호 빈도가 적어 CONDITIONAL 상태에 있고, cex_dex는 DEX 어댑터 구현이 제한적이라 아직 개발 중입니다.

전반적으로 cross_exchange와 statistical_arb는 이미 실용적 수준의 복잡성과 안정성을 갖추었으며, 수수료·슬리피지·데이터 품질 필터를 포함한 고급 기능을 적용해 기관급 전략에 근접한 완성도입니다. 다른 전략들은 기본 구조는 갖추었지만, 연구 보고서에서 제시된 OU half‑life 분석, 레짐 감지(레짐 전환 모델), LSTM 기반 신호 생성 등 고급 기술이 아직 반영되지 않아 조건부 또는 검증 단계에 있습니다. 전체 프로그램은 Python 3.12 + Rust (PyO3) 기반 비동기 구조, TimescaleDB + Redis + Prometheus 인프라, Pydantic 타입 검증, 4,800여 개 테스트와 86% 커버리지를 갖추고 있어 초기 버전보다 훨씬 체계적입니다.

이전 아비트라지 프로젝트와의 비교

과거 “arbitrage” 폴더 기반 프로젝트는 모듈 간 응집도가 낮고 중복 코드가 많아 전략 간 상호 운용과 확장성이 떨어졌습니다. 현재 leviathan-arbitrage는:

공통 베이스 클래스, 코스트 계산기, 슬리피지 모델 등 핵심 컴포넌트를 분리하여 전략 간 중복을 제거했습니다.

RiskGuardian·Circuit Breaker·Kill Switch 등 리스크 관리 체인을 갖추고, 라이브 전환을 위해 LiveGate 6‑check를 적용했습니다.

전략·인프라·대시보드가 하나의 파이프라인으로 통합되어, 테스트와 Shadow 운용을 거쳐 TF 단계로 진입하는 일관된 워크플로우를 제공합니다.

Shadow 환경에서 24H Progressive Shadow와 TF Final Canary 7일 테스트를 실행해 장기적 안정성과 수익성을 검증합니다.

따라서, 예전 프로젝트와 달리 지금은 **명확한 설계 문서(SSOT)**와 엄격한 검증 기준을 따르는 통합 시스템으로 발전했으며, 일부 전략은 이미 상용 기관급 수준에 근접하고 있습니다. 앞으로 TF SF Stage 3~6에서 연속 Shadow 테스트와 파라미터 튜닝을 통해 나머지 전략의 완성도를 높이고, TF Final을 통과하면 라이브 거래를 시작할 수 있을 것입니다.


데이터 정합성 검증 모듈의 필요성과 설계 방향을 명확히 하기 위해 현재 코드에 어떤 요소가 있는지부터 살펴보았습니다. LEVIATHAN 엔진에는 이미 **StaleOrderbookDetector**와 **HealthChecker** 같은 기반 기능이 있지만, 이들이 개별적으로 동작할 뿐 통합된 “데이터 품질 관리자”는 아직 존재하지 않습니다.

### 현행 기능 분석

* **StaleOrderbookDetector**는 4가지 레이어를 통해 데이터 이상을 감지합니다. 크로스‑익스체인지 가격 검증, heartbeat EMA 기반 갭 감지, 시퀀스 번호 누락 감지, 스프레드 이상치 검출을 포함하며, 이상이 발견되면 해당 `(거래소, 종목)`을 블랙리스트에 올립니다. heartbeat 레이어는 업데이트 간격이 EMA의 5배를 초과하면 stale로 간주합니다. `check_all_layers()`로 여러 레이어를 한 번에 실행할 수 있습니다.
* **HealthChecker**는 각 거래소의 연결 상태, API 응답 지연, 웹소켓 안정성, 주문 체결률을 가중 합으로 계산해 0~1 사이의 **health score**를 반환합니다.
* **LiveGate**는 신호의 Sharpe·MDD 외에 모든 거래소의 health score가 0.95 이상인지 확인하고, 이를 통과하지 못하면 라이브 전환을 차단합니다. **RiskGuardian** 역시 사전 거래검사에서 `exchange_health_scores`가 임계값보다 낮으면 주문을 거부합니다.
* Runbook에서 health score 계산식과 0.95 미만일 때 차단해야 한다는 기준을 제시하고 있습니다.

### 개선해야 할 점

현재 각 컴포넌트가 제각각 존재해 통합적 데이터 품질 관리가 어렵고, 수집된 지표가 RiskGuardian과 LiveGate로 전달되는 흐름도 명확하지 않습니다. 또한 StaleDetector의 이벤트가 metrics에 일부만 반영돼 있어 모니터링 및 알림 체계가 미흡합니다.

### 제안하는 데이터 정합성 검증 모듈(DataQualityManager) 설계

1. **중앙 관리 객체 설계:** `DataQualityManager` 클래스를 새로 만들어 각 거래소별 `HealthChecker` 인스턴스와 각 종목별 `StaleOrderbookDetector`를 보유하도록 합니다. 이 관리자는 다음 기능을 제공합니다.

   * orderbook 업데이트 시 `update_orderbook(exchange, symbol, book, seq)`를 호출하여 ① StaleDetector의 각 레이어를 실행하고 실패 시 `STALE_ORDERBOOK_REJECTED` 메트릭에 이유별 레이블을 증가시키며, 블랙리스트 처리를 합니다. ② 각 거래소의 heartbeat 및 Latency 정보를 업데이트하여 `HealthChecker`에 전달합니다.
   * API 호출 후 `record_api_latency(exchange, latency_ms)`를 호출하고, 주문 체결 결과에 따라 `record_order_fill(exchange, filled)`를 호출합니다.
   * 일정 주기(예: 1초마다)로 `get_health_scores()`를 호출하여 모든 거래소의 health score를 계산하고 `EXCHANGE_HEALTH_SCORE` 메트릭에 업데이트합니다.
   * 블랙리스트 TTL을 관리하는 `cleanup()` 메서드를 주기적으로 호출합니다.

2. **엔진 통합:**

   * `engine/src/main.py`와 각 Collector/Adapter에서 WebSocket 메시지를 받을 때마다 `DataQualityManager.update_orderbook()`을 호출하도록 연결합니다. API 호출 wrapper에서는 latency와 error를 `DataQualityManager`에 전달하도록 수정합니다.
   * RiskGuardian에서 포트폴리오 상태를 생성할 때 `DataQualityManager.get_health_scores()`를 호출하여 `exchange_health_scores`에 채워 넣습니다. LiveGate는 `exchange_health_fn`으로 `DataQualityManager.get_health_scores`를 주입하여 health check를 수행합니다.
   * StaleDetector가 블랙리스트에 추가한 종목은 signal generation 단계에서 반드시 걸러지도록 각 전략 코드에서 `DataQualityManager.is_blacklisted(exchange, symbol)` 검사를 추가합니다.

3. **Metrics 및 알림 강화:**

   * `engine/src/infra/metrics.py`에 heartbeat 실패, sequence gap, spread 이상치 등 레이어별 거부 건수를 세는 카운터를 추가하고, health score도 거래소별 Gauge로 지속적으로 기록합니다.
   * Grafana 대시보드에 “Stale Data Events” 패널과 “Exchange Health Score” 타임시리즈를 추가해 운영자가 이상 상태를 즉시 확인할 수 있도록 합니다.

4. **유연한 임계값 설정:** StaleDetector의 `deviation_pct`, `heartbeat multiplier`, `blacklist TTL` 등을 환경변수 또는 설정 파일에서 조정할 수 있도록 유지하여, Korean exchange와 국제 거래소에 다른 임계치를 적용하는 등 환경별 세밀한 조정이 가능하게 합니다.

5. **테스트 및 검증:** 단위 테스트에서 가짜 orderbook 업데이트를 주입해 각 레이어가 정상적으로 거부하는지 검증하고, health score가 낮아졌을 때 RiskGuardian과 LiveGate가 정확히 차단하는지 E2E 테스트를 추가합니다. Shadow Mode에서 일정 기간 모니터링하여 false positive와 false negative 비율을 측정하고 임계치를 보정합니다.

### 기대 효과

이러한 **DataQualityManager**를 도입하면, 데이터 오류나 통신 지연을 조기에 감지해 잘못된 신호를 차단하고, 각 거래소의 상태를 수치화해 리스크 관리 체계에 반영할 수 있습니다. 특히 자본 규모가 작은 개인 투자자는 한 번의 데이터 오류로도 큰 손실을 볼 수 있으므로, 데이터 정합성 검증 모듈을 통해 **제한된 자본을 보호하고 장기적으로 생존할 확률을 높일 수 있습니다**.
