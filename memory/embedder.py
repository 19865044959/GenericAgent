"""
embedder.py - 本地 embedding 模型封装（可插拔后端）

后端优先级:
  1. ONNX 后端 (onnxruntime + 本地模型) — 推荐，~10ms/次
  2. API 后端 (HTTP embedding API) — 需要网络，~200ms/次
  3. None — 降级为关键词匹配

用法:
    embedder = get_embedder()       # 单例，启动时加载
    if embedder:
        vec = embedder.encode("文本")  # → numpy array (384维)
"""

import os
import numpy as np

_MODEL_NAME = "all-MiniLM-L6-v2"
_MODEL_DIM = 384
_embedder_instance = None


class Embedder:
    """抽象基类"""
    def encode(self, text: str):
        raise NotImplementedError

    @property
    def dim(self) -> int:
        raise NotImplementedError

    @property
    def backend_name(self) -> str:
        raise NotImplementedError


class OnnxEmbedder(Embedder):
    """ONNX Runtime 后端

    自动检测模型目录下的 model.onnx + tokenizer.json。
    优先级: bge-small-zh-v1.5 (中文) > all-MiniLM-L6-v2 (英文)

    模型下载:
      from huggingface_hub import hf_hub_download
      # Chinese (recommended):
      #   hf_hub_download('Xenova/bge-small-zh-v1.5', 'onnx/model.onnx', local_dir='memory/models/bge-small-zh-v1.5')
      #   hf_hub_download('Xenova/bge-small-zh-v1.5', 'tokenizer.json', local_dir='memory/models/bge-small-zh-v1.5')
      # English (fallback):
      #   hf_hub_download('optimum/all-MiniLM-L6-v2', 'model.onnx', local_dir='memory/models/all-MiniLM-L6-v2')
    """

    # 模型搜索顺序: 中文优先
    _MODEL_CANDIDATES = [
        'bge-small-zh-v1.5',
        'all-MiniLM-L6-v2',
    ]

    def __init__(self, model_dir=None):
        import onnxruntime as ort

        if model_dir is None:
            model_dir = self._find_model()

        model_path = os.path.join(model_dir, 'model.onnx')
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"ONNX model not found at {model_path}. Download it first:\n"
                f"  hf_hub_download('Xenova/bge-small-zh-v1.5', 'onnx/model.onnx', "
                f"local_dir='{model_dir}')"
            )

        # Tokenizer
        tokenizer_path = os.path.join(model_dir, 'tokenizer.json')
        if os.path.exists(tokenizer_path):
            from tokenizers import Tokenizer
            self._tokenizer = Tokenizer.from_file(tokenizer_path)
        else:
            raise FileNotFoundError(f"Tokenizer not found at {tokenizer_path}")

        self._session = ort.InferenceSession(model_path)
        self._model_dir = model_dir

        # 输入/输出名称
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name

        # 从模型输出形状推断维度
        output_shape = self._session.get_outputs()[0].shape
        self._dim = output_shape[-1] if output_shape[-1] is not None else 384

    @classmethod
    def _find_model(cls):
        """在候选目录中寻找可用的 ONNX 模型"""
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
        for candidate in cls._MODEL_CANDIDATES:
            model_path = os.path.join(base, candidate, 'model.onnx')
            if os.path.exists(model_path):
                return os.path.join(base, candidate)
        # 返回默认路径（即使不存在，让后续报错）
        return os.path.join(base, cls._MODEL_CANDIDATES[0])

    def encode(self, text: str):
        """将文本编码为 [dim] 向量，返回 numpy array"""
        encoded = self._tokenizer.encode(text)
        input_ids = np.array([encoded.ids], dtype=np.int64)
        attention_mask = np.array([encoded.attention_mask], dtype=np.int64)

        inputs = {self._input_name: input_ids}
        for inp in self._session.get_inputs():
            if inp.name == 'attention_mask':
                inputs['attention_mask'] = attention_mask
            elif inp.name == 'token_type_ids':
                inputs['token_type_ids'] = np.zeros_like(input_ids)

        outputs = self._session.run([self._output_name], inputs)
        token_embeddings = outputs[0]
        mask_expanded = np.expand_dims(attention_mask, -1)
        masked = token_embeddings * mask_expanded
        summed = masked.sum(axis=1)
        counts = mask_expanded.sum(axis=1)
        mean_pooled = summed / counts

        vec = mean_pooled[0]
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.astype(np.float32)

    @property
    def dim(self) -> int: return self._dim

    @property
    def backend_name(self) -> str:
        model_name = os.path.basename(self._model_dir)
        return f"onnx/{model_name}"


class APIEmbedder(Embedder):
    """HTTP API 后端 — 用于调用兼容 OpenAI embedding 的接口"""

    def __init__(self, api_base=None, api_key=None, model=None):
        if api_base is None:
            api_base = os.environ.get('EMBED_API_BASE', '')
        if api_key is None:
            api_key = os.environ.get('EMBED_API_KEY', os.environ.get('OPENAI_API_KEY', ''))
        if model is None:
            model = os.environ.get('EMBED_MODEL', 'text-embedding-3-small')

        self._api_base = api_base.rstrip('/')
        self._api_key = api_key
        self._model = model

    def encode(self, text: str):
        import requests
        url = f"{self._api_base}/embeddings"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload = {"model": self._model, "input": text}
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        vec = np.array(data['data'][0]['embedding'], dtype=np.float32)
        # L2 normalize
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    @property
    def dim(self) -> int:
        # text-embedding-3-small → 1536, ada-002 → 1536
        return 1536

    @property
    def backend_name(self) -> str: return "api"


def get_embedder():
    """获取全局单例 embedder。按优先级尝试: ONNX → API → NgramFallback"""
    global _embedder_instance

    if _embedder_instance is not None:
        return _embedder_instance

    # 1. 尝试 ONNX
    try:
        _embedder_instance = OnnxEmbedder()
        print(f"[Embedder] Using ONNX backend: {_MODEL_NAME}")
        return _embedder_instance
    except (ImportError, FileNotFoundError) as e:
        pass

    # 2. 尝试 API
    try:
        api_base = os.environ.get('EMBED_API_BASE', '')
        if api_base:
            _embedder_instance = APIEmbedder()
            print(f"[Embedder] Using API backend: {_embedder_instance._model}")
            return _embedder_instance
    except Exception:
        pass

    # 3. Ngram 降级（纯 numpy，零依赖）
    print("[Embedder] Using NgramFallback backend (lightweight, "
          "install onnxruntime for better semantics)")
    _embedder_instance = NgramFallbackEmbedder()
    return _embedder_instance


class NgramFallbackEmbedder(Embedder):
    """纯 numpy 的 n-gram 向量化 fallback。

    虽然不如 transformer 模型精准，但比纯关键词匹配好：
    - 中文: 提取 1-3 字 n-gram，捕捉子串语义
    - 英文: 提取单词 + 3-char n-gram
    - 用 TF-IDF 风格加权（常见 n-gram 降权）
    - 384 维，与 all-MiniLM-L6-v2 维度一致，方便后续无缝切换

    纯 numpy 实现，零外部依赖。单次编码 < 1ms。
    """

    DIM = 384  # 与 all-MiniLM-L6-v2 对齐

    def __init__(self):
        import numpy as np
        self._rng = np.random.RandomState(42)
        # 为常见的中文 n-gram 预生成伪随机向量（确定性，保证同词同向量）
        self._cache: dict[str, np.ndarray] = {}

    @staticmethod
    def _extract_features(text: str) -> list[str]:
        """提取文本的特征 n-gram"""
        features = []

        # 中文: uni-gram, bi-gram, tri-gram
        chinese = ''.join(c for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')
        for i in range(len(chinese)):
            features.append(f'C1:{chinese[i]}')
            if i + 1 < len(chinese):
                features.append(f'C2:{chinese[i:i+2]}')
            if i + 2 < len(chinese):
                features.append(f'C3:{chinese[i:i+3]}')

        # 英文: 单词 + char 3-gram
        words = [w.lower() for w in __import__('re').findall(r'[a-zA-Z]{2,}', text)]
        for w in words:
            features.append(f'W:{w}')
            for i in range(len(w) - 2):
                features.append(f'E3:{w[i:i+3]}')

        return features

    def _get_vec(self, feature: str) -> np.ndarray:
        """为特征生成确定性向量（伪哈希 → 归一化向量）"""
        if feature not in self._cache:
            # 用 hash 做种子，生成确定性随机向量
            seed = hash(feature) & 0x7fffffff
            rng = np.random.RandomState(seed)
            vec = rng.randn(self.DIM).astype(np.float32)
            # L2 归一化
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            self._cache[feature] = vec
        return self._cache[feature]

    def encode(self, text: str):
        """将文本编码为 [384] 向量"""
        features = self._extract_features(text)
        if not features:
            return np.zeros(self.DIM, dtype=np.float32)

        # 统计特征频率
        from collections import Counter
        counts = Counter(features)

        # TF-IDF 权重: 高频特征降权
        total = len(features)
        vec = np.zeros(self.DIM, dtype=np.float32)
        for feat, count in counts.items():
            tf = count / total  # 词频
            idf = 1.0 / (1.0 + count)  # 逆文档频率（简化：特征越常见权重越低）
            weight = tf * idf
            vec += self._get_vec(feat) * weight

        # L2 归一化
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    @property
    def dim(self) -> int: return self.DIM

    @property
    def backend_name(self) -> str: return "ngram"


def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """两个 L2 归一化向量的余弦相似度（已归一化则退化为点积）"""
    return float(np.dot(vec1, vec2))
