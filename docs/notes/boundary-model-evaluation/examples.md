# 小模型切句样例：实测原始输出

竖线表示切点。WtP 使用未适配阈值 0.01；SaT 使用 0.25。此处不额外施加标点、长度、词边界规则。

## wtp-bert-tiny

### screenshot-office (en)

```text
For a spot to sit in this room with me in person here in our office, to which people have offered tens of thousands of dollars to get such a seat and been told no.
```

### screenshot-opportunity (en)

```text
Second, this will be an even better opportunity than the ones I've opened up the last |  3 times, which have generated |  $55.6 million in tracked earnings.
```

### screenshot-colon (en)

```text
Just so you understand why this is so crucial: |  the last time |  I opened this opportunity, every single spot was gone faster than I could announce it live.
```

### screenshot-minutes (en)

```text
Members of this group are going to get access to the offer link, |  a full |  15 minutes before it goes live to everyone else.
```

### en-clauses (en)

```text
We finished the first part, but the second part took longer, so we changed the plan.
```

### en-list (en)

```text
We bought apples, oranges, |  and bananas for the children.
```

### en-sentences (en)

```text
We finished the first part. |  The second part took longer. |  We changed the plan.
```

### en-name (en)

```text
The meeting with |  New |  York |  University starts at |  3.14 on the old clock, |  according to the note.
```

### ja-clauses (ja)

```text
準備が終わったので、これから配信を始めますが、音が聞 | こえなかったら教えてください。
```

### ja-list (ja)

```text
今日は、りんご、みかん、バナナを買って帰りました。
```

### ja-sentences (ja)

```text
準備が終わりました。 | これから配信を始めます。 | 音を確認してください。
```

### es-clauses (es)

```text
Ya terminamos la primera parte, |  pero la segunda tomó más tiempo, |  así que cambiamos el plan.
```

### es-list (es)

```text
Compramos manzanas, naranjas y plátanos para los niños.
```

### es-sentences (es)

```text
Terminamos la primera parte. |  La segunda tomó más tiempo. |  Cambiamos el plan.
```

### ko-clauses (ko)

```text
준비가 끝났으니 이제 방송을 시작하겠습니다만, 소리가 들리지 않으면 채팅으로 알려 주세요.
```

### ko-list (ko)

```text
오늘은 사과, |  오렌지, 바나나를 사 왔습니다.
```

### ko-sentences (ko)

```text
준비가 끝났습니다. |  이제 방송을 시작하겠습니다. |  소리를 확인해 주세요.
```

## wtp-bert-mini

### screenshot-office (en)

```text
For a spot to sit in this room with me in person here in our office, to which people have offered tens of thousands of dollars to get such a seat and been told no.
```

### screenshot-opportunity (en)

```text
Second, this will be an even better opportunity than the ones I've opened up the last 3 times, which have generated $55.6 million in tracked earnings.
```

### screenshot-colon (en)

```text
Just so you understand why this is so crucial: |  the last time |  I opened this opportunity, every single spot was gone faster than I could announce it live.
```

### screenshot-minutes (en)

```text
Members of this group are going to get access to the offer link, a full 15 minutes before it goes live to everyone else.
```

### en-clauses (en)

```text
We finished the first part, but the second part took longer, |  so we changed the plan.
```

### en-list (en)

```text
We bought apples, oranges, and bananas for the children.
```

### en-sentences (en)

```text
We finished the first part. |  The second part took longer. |  We changed the plan.
```

### en-name (en)

```text
The meeting with New York University starts at 3.14 on the old clock, according to the note.
```

### ja-clauses (ja)

```text
準備が終わったので、これから配信を始めますが、音が聞こえなかったら教えてください。
```

### ja-list (ja)

```text
今日は、りんご、みかん、バナナを買って帰りました。
```

### ja-sentences (ja)

```text
準備が終わりました。 | これから配信を始めます。 | 音を確認してください。
```

### es-clauses (es)

```text
Ya terminamos la primera parte, pero la segunda tomó más tiempo, así que cambiamos el plan.
```

### es-list (es)

```text
Compramos manzanas, |  naranjas y plátanos para los niños.
```

### es-sentences (es)

```text
Terminamos la primera parte. |  La segunda tomó más tiempo. |  Cambiamos el plan.
```

### ko-clauses (ko)

```text
준비가 끝났으니 이제 방송을 시작하겠습니다만, 소리가 들리지 않으면 채팅으로 알려 주세요.
```

### ko-list (ko)

```text
오늘은 사과, 오렌지, |  바나나를 사 왔습니다.
```

### ko-sentences (ko)

```text
준비가 끝났습니다. |  이제 방송을 시작하겠습니다. |  소리를 확인해 주세요.
```

## sat-3l-sm

### screenshot-office (en)

```text
For a spot to sit in this room with me in person here in our office, to which people have offered tens of thousands of dollars to get such a seat and been told no.
```

### screenshot-opportunity (en)

```text
Second, this will be an even better opportunity than the ones I've opened up the last 3 times, which have generated $55.6 million in tracked earnings.
```

### screenshot-colon (en)

```text
Just so you understand why this is so crucial: the last time I opened this opportunity, every single spot was gone faster than I could announce it live.
```

### screenshot-minutes (en)

```text
Members of this group are going to get access to the offer link, a full 15 minutes before it goes live to everyone else.
```

### en-clauses (en)

```text
We finished the first part, but the second part took longer, so we changed the plan.
```

### en-list (en)

```text
We bought apples, oranges, and bananas for the children.
```

### en-sentences (en)

```text
We finished the first part. |  The second part took longer. |  We changed the plan.
```

### en-name (en)

```text
The meeting with New York University starts at 3.14 on the old clock, according to the note.
```

### ja-clauses (ja)

```text
準備が終わったので、これから配信を始めますが、音が聞こえなかったら教えてください。
```

### ja-list (ja)

```text
今日は、りんご、みかん、バナナを買って帰りました。
```

### ja-sentences (ja)

```text
準備が終わりました。 | これから配信を始めます。 | 音を確認してください。
```

### es-clauses (es)

```text
Ya terminamos la primera parte, pero la segunda tomó más tiempo, así que cambiamos el plan.
```

### es-list (es)

```text
Compramos manzanas, naranjas y plátanos para los niños.
```

### es-sentences (es)

```text
Terminamos la primera parte. |  La segunda tomó más tiempo. |  Cambiamos el plan.
```

### ko-clauses (ko)

```text
준비가 끝났으니 이제 방송을 시작하겠습니다만, 소리가 들리지 않으면 채팅으로 알려 주세요.
```

### ko-list (ko)

```text
오늘은 사과, 오렌지, 바나나를 사 왔습니다.
```

### ko-sentences (ko)

```text
준비가 끝났습니다. |  이제 방송을 시작하겠습니다. |  소리를 확인해 주세요.
```
