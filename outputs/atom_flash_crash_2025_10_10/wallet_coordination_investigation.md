# ATOM/USDTフラッシュクラッシュ：特定user・wallet関与仮説の検証

## 結論

公開データから「特定の自然人またはCosmos walletが意図的に仕掛けた」とは結論できない。

一方、2025-10-10 21:20:37.689043 UTCの最終的な下ヒゲについては、同一マイクロ秒・連続trade ID・全件売り主導の92約定（合計9,695.33 ATOM）が、1.688 USDTから0.001 USDTまで価格帯を横断している。これは、単一または極めて密に同期した攻撃的な売り注文エピソードと強く整合する。ただし、Binance公開データにはtaker order IDもaccount IDもないため、注文数・取引主体・意図は確定できない。

Cosmos Hub側では、最安値の19.52秒前のヘッダー時刻を持つブロックに、観測対象のBinanceサービスアドレス宛て2,000 ATOM送金が含まれる。これは取引実行時刻や取引所の入金反映時刻ではない。送金元が多数の相手先と複数の入金memoを扱うことはサービス運用と整合的だが、所有者や役割は確定できない。2,000 ATOMは売りエピソードの20.6%に相当する。さらに直前30分・2時間の当該アドレスへの流入量は30日対照で例外的ではなく、送信元集中度も4日間の補助対照では異常な集中を示さない。このwalletと売り注文を結ぶ公開証拠はない。

証拠評価は次のとおりである。

| 仮説 | 評価 |
|---|---|
| 一つの強い売り注文エピソードが最終下ヒゲを作った | 強く支持。ただし単一order IDは非公開 |
| 一つ以上のBinance accountが最終下ヒゲを発生させた | 構造上は当然にあり得るが、公開データではaccount帰属不能 |
| 直前のCosmos送信元walletがその売りaccountである | 支持されない |
| 直前に観測対象のBinanceサービスアドレスへ異常なATOM集積があった | 検討した対照では支持されない |
| 複数walletによる協調操作だった | 確立されない |
| 特定の自然人・beneficial ownerを識別できる | 公開データでは不可 |

## 1. 市場側で分かったこと

記事は「4ドルから0.001ドルに一瞬で」と説明しているが、逐次約定では段階が分かれる。[CRYPTO TIMESの記事](https://crypto-times.jp/news-atom-drops-from-4-to-0-001-in-an-instant-as-a-terrifying-flash-crash-occurs/)が述べる市場全体の下落と、ATOM/USDT固有の最終下ヒゲは分離して扱う必要がある。

| 時刻（UTC） | 公開約定で確認した水準 |
|---|---:|
| 16:41:10.471671 | 4.000 USDT以上での最後の約定 |
| 21:13:25.881040 | 3.500 USDT以下 |
| 21:16:50.360555 | 3.000 USDT以下 |
| 21:19:08.960865 | 2.000 USDT以下 |
| 21:20:36.189535 | 1.700 USDT以下 |
| 21:20:37.689043 | 1.000、続いて0.001 USDT |

21:20:37.689043 UTCだけを切り出すと、以下を確認した。

- raw tradeは92件、trade ID 254107861–254107952で欠番なし。
- すべて `buyer is maker = true` であり、aggressive seller側の約定である。
- 合計9,695.33 ATOM、約定代金9,170.080188 USDT、VWAP 0.945824 USDT。
- 最初の約定1.688 USDTから最安値0.001 USDTまで、同一マイクロ秒内で99.9408%下落。
- aggregate tradeは71行。Binanceの定義では、aggTradeは同一taker order・同一時刻・同一価格の約定を集約する。[Binance Spot API glossary](https://developers.binance.com/en/docs/products/spot/faqs/spot_glossary)、[Binance public-data schema](https://github.com/binance/binance-public-data/blob/master/README.md)

異なる価格のaggTradeを共通order IDで結ぶ列は公開されていない。このため、観測列は「一つの大きなmarket/marketable sellが板を掃いた」場合とも整合的だが、単一注文か複数の同時注文かは公開データでは区別できない。

また、本研究で取得したBinanceデータに過去のorder-book snapshotはない。したがって「流動性が枯渇したことを証明した」とは表現しない。観測された多数の価格帯を横断する売り約定は、買い板が薄い・価格帯間の深さが小さい状態と整合的である、とだけ述べる。Binanceは後日の説明で、ATOM等の古いlimit buyが残る一方、極端な売り局面で買い注文が不足したという見解を示しているが、これは取引所側説明であり、独立した板データによる検証ではない。[Binance公式発表への案内](https://t.me/s/binance_announcements?before=7815)、[発表内容の転載](https://fxnewsgroup.com/forex-news/cryptocurrency/binance-issues-statement-on-recent-market-volatility/)

## 2. 最も近いon-chain送金

次の送金を含むCosmos Hub block 27,908,324のヘッダー時刻は、最安値より19.521571秒前である。最安値時刻に対応付けたHub block 27,908,328とは4 blockの差がある。Binanceの約定自体がHubブロックに含まれるわけではない。

| 項目 | 値 |
|---|---|
| ブロックヘッダー時刻 | 2025-10-10 21:20:18.167472110 UTC |
| tx hash | `0DF333AAA3B832D92E0F0FC920D1DC51170CA192BAA2BC407F4158B1939CD153` |
| 送信元 | `cosmos1qml5yxnfgdhcywp2w2lpvuj9fazzeylg2gdaeq` |
| 受信先 | `cosmos1j8pp7zvcu9z8vd882m284j29fn2dszh05cqvf9`（公開記録上のBinance service address） |
| 数量 | 2,000 ATOM |
| memo | 非公開化したcluster ID `memo_sha256_5abe7361829a` |
| 売りエピソード9,695.33 ATOMに対する比率 | 20.63% |

時間的には注目すべき近接である。しかし、同じ内部accountにcreditされ、そのaccountが19.52秒以内に9,695.33 ATOMを売ったと示す公開join keyは存在しない。特に、ブロックヘッダー時刻は取引実行時刻やBinance deposit-credit時刻を表さない。

この送信元walletの4日間fingerprintは、単一userの自己管理walletよりサービス運用・withdrawal routingに近い。

- incoming 315件・1,405,924.236174 ATOM、outgoing 449件・1,124,647.862346 ATOM。
- counterpartyは228 address。
- Binance宛ては59件・368,619.397651 ATOM、10種類のmemo cluster。
- incomingの77.23%（1,085,802.916922 ATOM）が公開記録上のKuCoin service addressから到来。
- Binance以外にも、公開記録上のMEXC・HitBTC service addressへ送金。
- 直前の2,000 ATOMと同じmemo cluster・同じ数量の送金は、保持4日間に合計5回・10,000 ATOMある。

このaddressの活動は、exchangeまたは仲介serviceが複数顧客の出金を処理するoperational walletの場合とも整合的であり、「特定user本人のwallet」とは断定できない。公開ラベルはserviceとの関係を示すだけで、所有権や業務上の役割を確定しない。

## 3. 直前流入は異常だったか

30日baselineは観測対象の1件のBinance service addressだけを抽出した5分bucketを使用した。以下の「Binance流入」はこのアドレスへの流入を指し、取引所全体の流入ではない。イベント側も5分境界にそろえ、21:20:00 UTCまでを比較している。したがって、上記21:20:18の2,000 ATOMは30日比較には含まれない。これを含む秒単位の4日比較も別途示す。

### 30日baseline

| 直前窓 | Binance流入 | 経験的percentile | 上側plus-one p | 判定 |
|---:|---:|---:|---:|---|
| 30分 | 12,887.657879 ATOM | 74.04 | 0.260 | 異常域でない |
| 2時間 | 49,647.000601 ATOM | 74.02 | 0.260 | 異常域でない |
| 6時間 | 204,522.656797 ATOM | 79.95 | 0.201 | 異常域でない |
| 24時間 | 797,701.141021 ATOM | 84.63 | 0.154 | 異常域でない |

5分ずつずらしたwindowは重複し系列相関を持つため、p値は記述的である。非重複windowでも30分p=0.260、2時間p=0.266で結論は同じだった。

### 秒単位の4日比較

| 直前窓 | 流入量 | 流入量percentile | top sender share | sender HHI | HHI percentile |
|---:|---:|---:|---:|---:|---:|
| 30分 | 14,887.657879 ATOM | 56.92 | 14.85% | 0.08998 | 0.018 |
| 2時間 | 51,647.000601 ATOM | 44.57 | 42.91% | 0.23135 | 53.25 |

直前30分はむしろ通常より著しく分散している。直前2時間の集中度は4日内の中央付近である。保持期間が4日しかないsender-level比較は補助分析だが、「少数walletが異常に集中して持ち込んだ」という見方とは一致しない。

## 4. 大口senderと共通資金源

直前2時間の最大Binance senderは次のaddressである。

`cosmos1ze3f954mtj30st8dw2qhylfvvtdv5q6x0e4k4q`

- 直前2時間に22,159.375 ATOM、Binance流入の42.91%。
- 最後のBinance送金は最安値の980.34秒前（約16分20秒前）で、数量681.715 ATOM。
- 4日間にBinance宛て445回・429,055.194981 ATOM、すべて同一memo cluster。
- OsmosisとのATOM IBC一致が893件。inbound 616件・431,382.007085 ATOM、outbound 277件・365,222.798906 ATOM。
- Bank送金の受取元は7アドレス（うち1アドレスは0.000003 ATOMの微少送金）であり、Binance宛ての反復送金を行う。

このfingerprintは、手作業の単発移転より、cross-chain routing・arbitrage・market-making等の自動運用と整合的である。活動主体は特定できず、悪意も推定できない。

top 10 senderのうち3 addressは、複数の共通資金源から繰り返しfundingされていた。最大の共通sourceは次である。

`cosmos1l0znsvddllw9knha3yx2svnlxny676d8ns7uys`

このsourceは4日間に公開記録上のBinance service addressから3,649,051.323165 ATOMを受け取り、Bank送金の送受信相手は518アドレス（受取元3・送信先515）であった。この件数は送信先だけでなく受信元も含む。共通sourceからの資金供給は、exchange hot-wallet／withdrawal infrastructureが複数の下流walletへ送金する場合とも整合的であり、「複数userの協調」を確立しない。wallet間の共通資金源を、そのまま共通beneficial ownerとみなしてはならない。

## 5. 時間近接性のlook-elsewhere確認

保持4日間では観測対象のBinanceサービスアドレス宛て送金が3,165件あり、標本中の任意の秒が何らかの送金ブロックのヘッダー時刻の後19.52秒以内となる割合は15.97%だった。2,000 ATOM以上の送金に限定すると2.25%、同じ送信元では0.33%、同じmemo clusterでは0.028%である。

ただし、これらはいずれもイベントを見た後に時間差や最寄り送金・数量・sender・memoを選んだpost-selection統計であり、confirmatory p値ではない。多数のaddress・memo・時間窓から最も近いものを選べば、偶然の一致を過大評価する。論文本文と同様に、探索的な時間的近接として解釈する。

## 6. 何があれば帰属できるか

特定accountまたはuserへの帰属には、少なくとも次の非公開記録が必要である。

1. Binanceのtaker order ID、account/subaccount ID、order type、clientOrderId、API key/IP audit、liquidation flag。
2. Binanceのdeposit-credit時刻と、on-chain memo clusterから内部accountへのmapping。
3. 21:20:37.689043 UTC直前のATOM/USDT order-book snapshot。
4. 他exchangeを含むaccount-levelのwithdrawal/deposit/order記録。

これらは取引所の内部調査、規制当局・捜査機関の照会、または当事者による署名付き開示がなければ取得できない。現在の証拠水準で特定addressを「攻撃者」「操作主体」と表記してはならない。

## 再現用artifact

- `results/wallet_coordination_analysis.json`：全統計とevidence assessment
- `results/wallet_coordination_candidates.csv`：直前2時間の上位15 sender
- `results/flash_sell_sequence.csv`：最安値マイクロ秒の71 aggTrade行
- `results/wallet_coordination_verification.json`：primary fileからの独立再計算（11 checks、PASS）
- `results/wallet_coordination_processed_verification.json`：公開中間データのみの検証（9 checks、PASS）。raw市場ハッシュ・92生約定・71集約約定の照合は明示的にスキップし、上記full検証とは区別する。
- `scripts/analyze_wallet_coordination.py`：分析script
- `scripts/verify_wallet_coordination.py`：検証script

公開約定データの列定義とmicrosecond化はBinance公式public-data repositoryに従った。Cosmos addressのexchange labelは公開記録とのexact-address matchであり、自然人または各取引senderの所有権を意味しない。
