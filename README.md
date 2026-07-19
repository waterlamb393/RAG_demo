このファイルは、ローカルRAG（LLM）の実証用コードです。

＜20260624_engineer_unite_meetup_vol11_lightningTalk.pdf＞
 engineer_unite_meetup_vol.11(2026/6/24)で発表したライトニングトークのプレゼンテーション資料

＜pdf_text_crawler_postgres.py＞
　PDF、HTML取得用のクローラ（指定サイトのリンクを辿ってPDFをDL。ログをDBに格納して重複DL防止）

＜serchengine_crawler_postgres_modifiedcheck.py＞
　サーチエンジン（Brave使用）の検索結果を取得するクローラ（同URL＋更新情報で重複DLを防止）
 
＜rag_1024dim.ipynb＞
　RAG検証用コード（Jupyter NoteBook）。Embbeding処理してDBに登録したベクトルからRAG推論（llama.cpp）を実行
 （モデルロード、推論の標準を生出力（確認用））
 　Embbedingモデル：BAAI/bge-m3（1024次元）
   推論モデル：Qwen3-8B-Q4_K_M.gguf（RTX4060の場合、このモデルがGPUに乗せられる限界）
 
 ＜rag_ollama_1024dim.ipynb＞
　RAG検証用コード（Jupyter NoteBook）。Embbeding処理してDBに登録したベクトルからRAG推論（ollama）を実行
 （推論速度のチューニングとして、ollamaを採用。現時点で最速）
 　Embbedingモデル：nomic-embed-text（ollamaでは、このモデルが要求された（768次元））
   推論モデル：Qwen3-8B-Q4_K_M.gguf（RTX4060の場合、このモデルがGPUに乗せられる限界）
 
 ＜unstructured_practice.ipynb＞
 　RAG用のデータベースのチャンキング前処理として技術検討用のデモ。
  （推論結果の精度が今一つのため、PDFおよびHTMLからのテキスト抽出だけでは不十分のため）
 
