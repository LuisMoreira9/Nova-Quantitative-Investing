<script lang="ts">
    import Book from "@lucide/svelte/icons/book";
    import GraduationCap from "@lucide/svelte/icons/graduation-cap";
    // import Github from "@lucide/svelte/icons/github";
    import { Card, CardHeader, CardTitle, CardContent } from '$lib/components/ui/card';

    let { data } = $props();
</script>

<section class="@container bg-background py-24">
    <div class="mx-auto max-w-3xl px-6">
        <div class="space-y-4">
            <h1 class="font-serif text-4xl font-medium text-balance">Free resources to learn quantitative finance</h1>
            <p class="text-muted-foreground">Curated, high-quality and free paths to learn the core skills used in quantitative finance: math, statistics, programming, data, and trading systems. Start with fundamentals and follow the learning sequence in each category.</p>
        </div>

        <div class="mt-10 grid grid-cols-1 gap-6 @md:grid-cols-2">
            <Card>
                <CardHeader>
                    <CardTitle class="flex items-center gap-2"><GraduationCap class="size-4 text-muted-foreground" />Courses</CardTitle>
                </CardHeader>
                <CardContent>
                    <ul class="space-y-4 text-sm text-muted-foreground">
                        <li>
                            <a class="font-medium text-foreground" href="https://www.coursera.org/learn/machine-learning" target="_blank" rel="noreferrer">Machine Learning (Andrew Ng) — Coursera</a>
                            <div class="text-xs">Free to audit; excellent ML foundations used across quant research.</div>
                        </li>

                        <li>
                            <a class="font-medium text-foreground" href="https://ocw.mit.edu" target="_blank" rel="noreferrer">MIT OpenCourseWare — Finance & Math courses</a>
                            <div class="text-xs">Search OCW for probability, stochastic processes, and financial engineering lecture notes.</div>
                        </li>

                        <li>
                            <a class="font-medium text-foreground" href="https://www.coursera.org/specializations/quantitative-finance" target="_blank" rel="noreferrer">Quantitative Finance Specializations — Coursera / edX</a>
                            <div class="text-xs">Many specializations offer free auditing — good for structured study tracks.</div>
                        </li>
                    </ul>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle class="flex items-center gap-2"><Book class="size-4 text-muted-foreground" />Books & papers</CardTitle>
                </CardHeader>
                <CardContent>
                    <ul class="space-y-4 text-sm text-muted-foreground">
                        <li>
                            <a class="font-medium text-foreground" href="https://quantstart.com" target="_blank" rel="noreferrer">QuantStart articles & tutorials</a>
                            <div class="text-xs">Practical guides on algorithmic trading, backtesting, and systematic strategies.</div>
                        </li>

                        <li>
                            <a class="font-medium text-foreground" href="https://papers.ssrn.com/" target="_blank" rel="noreferrer">SSRN / arXiv — Research papers</a>
                            <div class="text-xs">Search for foundational and contemporary quant finance research; many papers are free.</div>
                        </li>

                        <li>
                            <a class="font-medium text-foreground" href="https://www.quantocracy.com/" target="_blank" rel="noreferrer">Quantocracy — Blog aggregator</a>
                            <div class="text-xs">Curated links to high-quality blog posts, tutorials and papers across the quant ecosystem.</div>
                        </li>
                    </ul>
                </CardContent>
            </Card>
        </div>

        <div class="mt-8 grid grid-cols-1 gap-6 @md:grid-cols-3">
            <Card>
                <CardHeader>
                    <CardTitle class="font-medium">Data & APIs</CardTitle>
                </CardHeader>
                <CardContent>
                    <ul class="space-y-3 text-sm text-muted-foreground">
                        <li><a class="font-medium text-foreground" href="https://www.kaggle.com/datasets" target="_blank" rel="noreferrer">Kaggle Datasets</a> — Many financial datasets and competitions.</li>
                        <li><a class="font-medium text-foreground" href="https://finance.yahoo.com/" target="_blank" rel="noreferrer">Yahoo Finance</a> — Historical prices via download and community APIs.</li>
                        <li><a class="font-medium text-foreground" href="https://www.alphavantage.co/" target="_blank" rel="noreferrer">Alpha Vantage</a> — Free API keys for price and technical indicators (limited).</li>
                    </ul>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle class="font-medium">Tools & libraries</CardTitle>
                </CardHeader>
                <CardContent>
                    <ul class="space-y-3 text-sm text-muted-foreground">
                        <li><span class="font-medium text-foreground">Python stack</span>: numpy, pandas, scipy, scikit-learn, statsmodels, matplotlib.</li>
                        <li><span class="font-medium text-foreground">Backtesting</span>: backtrader, zipline (community forks), bt.</li>
                        <li><a class="font-medium text-foreground" href="https://github.com/" target="_blank" rel="noreferrer">Explore GitHub</a> — Search for open-source quant projects to study code.</li>
                    </ul>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle class="flex items-center gap-2">
                        <!-- <Github class="size-4 text-muted-foreground" />-->
                         Communities</CardTitle> 
                </CardHeader>
                <CardContent>
                    <ul class="space-y-3 text-sm text-muted-foreground">
                        <li><a class="font-medium text-foreground" href="https://quant.stackexchange.com/" target="_blank" rel="noreferrer">Quantitative Finance StackExchange</a></li>
                        <li><a class="font-medium text-foreground" href="https://www.reddit.com/r/algotrading/" target="_blank" rel="noreferrer">r/algotrading</a> and <a class="font-medium text-foreground" href="https://www.reddit.com/r/quant/" target="_blank" rel="noreferrer">r/quant</a></li>
                        <li><a class="font-medium text-foreground" href="#" target="_blank" rel="noreferrer">Nova Quant Club Whatsapp Group</a></li>
                    </ul>
                </CardContent>
            </Card>
        </div>

        <p class="mt-8 text-sm text-muted-foreground">Tip: Start with strong programming and statistics fundamentals, then focus on domain-specific math (stochastic calculus, time series), data engineering, and finally system design and risk management for production strategies.</p>
    </div>
</section>

