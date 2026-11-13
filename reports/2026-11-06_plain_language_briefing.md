# Plain-language bilingual team briefing

## 中文

我们目前主要完成了两部分工作。第一部分是把金融文本处理、切块、向量生成和结果追踪这条流程搭起来。第二部分是用五家公司的固定年报文本，实际跑了两组小实验：一组调整每块的长度，另一组保持块数不变，只移动内部切点。

结果是，调整长度时，微软在四个设置对照里的前两个近邻集合发生了变化；只移动边界时，这次所有公司的前两个近邻集合都没变，但相似度分数和部分排序还是会变。所以现在可以说，处理方式会影响这组固定输入的表示结果，还不能说模型普遍不稳定，更不能说已经证明会影响收益。

我们也做了一个能找回原文证据的检索助手，但目前没有生成答案，也没有运行真正的MoE。正式金融预测还没开始。接下来除了拿到历史数据，还要和导师确认研究口径，检查公司和证券的历史对应关系，以及每条信息在决策时是否已经可用。等这些条件都满足，再比较只用价格的模型和加入文本后的模型。

## English

We have mainly completed two pieces of work. First, we built the pipeline for processing financial text, splitting it into chunks, producing embeddings and tracing the outputs back to fixed inputs and settings. Second, we ran two small experiments on five fixed annual-report extracts. One changed chunk length; the other kept the number of chunks fixed and shifted the internal boundaries.

In the length experiment, Microsoft’s top-two peer set changed in four setting comparisons. In the boundary experiment, none of the top-two sets changed, although similarity scores and some rankings still moved. This shows sensitivity within this particular setup. It does not establish general instability or an effect on returns.

We also built a retrieval assistant that returns source evidence. It has not generated answers or run a genuine MoE model. Formal financial prediction has not started. The next step requires both actual historical data and agreed research rules: which firms and securities enter the sample, how they link over time, what outcome we predict, and which information was available before each decision. Once those conditions are met, we can fairly compare a price-only model with models that also use text. We are not claiming predictive gains or economic value yet.
