import base64
import os
import re
from typing import Literal, Optional, Sequence

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionContentPartImageParam,
    ChatCompletionContentPartTextParam,
    ChatCompletionUserMessageParam,
)
from pydantic import BaseModel
from cairosvg import svg2png

import streamlit as st
import streamlit_authenticator as stauth


generate_prompt = """
あなたは、優秀なイラストレーターです。
添付の画像を使って、「{scene}」のイラストを描いて下さい。
添付の画像をどのような向きや位置、スケールで配置するかのレイアウトによってシーンを表現するイラストを作成して下さい。
1つの画像を複数回使用したり、1度も使用しない画像があっても問題ありません。
レイアウトの結果はSVGで出力して下さい。
SVGは以下のように、`<svg>`タグで囲まれたものを出力しつつ、添付の画像を`<image>`タグで埋め込んだものを出力して下さい。
必要であれば`<rect>`, `<circle>`, `<eclipse>`, `<line>`, `<polygon>`, `<path>`といったプリミティブな図形も使用して下さい。
```svg
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">
  <rect width="800" height="300" x="0" y="300" fill="brown"/>
  <image href="house.png" width="200" height="200" x="50" y="200"/>
  <image href="company.png" width="300" height="200" x="500" y="300"/>
  <image href="woman.png" width="100" height="200" x="210" y="200" transform="scale(-1,1) translate(-400,0)"/>
</svg>
```


#### 以下にレイアウトを生成する際の注意点を例を挙げながら説明します。
仮に、男性のイラスト(male.png)と、女性のイラスト(female.png)とテーブルのイラスト(table.png)が添付されており、
「テーブルを挟んで男性と女性が話している」というシーンを描く場合、添付の画像から以下の点に注意してレイアウトを考えます。

* テーブルが男性と女性の間に来るように配置する(SVGのimageタグ内の`transform`で`translate`や`rotate`を使用し、配置を調整する)。
* 男性と女性は会話をしているので、画像のミラーリングを行い、向きを向かい合わせにする(SVGのimageタグ内の`transform`で`scale(-1,1) translate(...)`を使用し、人物の向きを調整する)。
* テーブルのサイズと男性と女性のサイズが不自然にならないように、スケールを調整する(SVGのimageタグ内の`width`や`height`を調整する)。
* 必要に応じて、その他のオブジェクト、地面・背景などでプリミティブな図形で表現できるものがあれば、プリミティブな図形で表現する(`<rect>`, `<circle>`, `<eclipse>`, `<line>`, `<polygon>`, `<path>`を挿入する)。
* 描画の順序によって要素が隠れないように注意する(描画順序を調整する、背景を先に描画する)。


#### ここからが実際のレイアウトの生成です。
SVGを出力して下さい。
画像ファイルは以下の順で添付されています。
{image_filenames}

出力: 
"""


adjusting_points = [
    "画像のスケールが不自然になっていないか確認し、`width`や`height`を調整する。",
    "画像が重なりすぎていないか確認し、`x`や`y`を調整する。",
    "向きや位置が不自然になっていないか確認し、`transform`に`rotate`や`scale(-1,1)`を追加し調整する。",
    "描画の順序がおかしくないか確認し、要素の順序を入れ替える。",
]


refine_prompt = """
添付の{output_image}画像は、以下の指示内容から生成されたSVG画像をレンダリングしたものです。
あなたはイラストのレイアウトを修正し改善するエキスパートです。
添付の画像のおかしな点を修正し、SVGを出力して下さい。


#### 指示内容
```plaintext
{previous_prompt}
```

#### 生成されたSVG
```svg
{previous_svg_code}
```

#### ここからが実際の指示
添付の画像を参考にして、「{scene}」を描いたSVGを修正し、出力して下さい。
画像ファイルは以下の順で添付されています。
{image_filenames}
以下の点に注意して修正を行って下さい。

{adjusting_points}
* 特に修正点がない場合は、前回のSVGをそのまま出力する。

出力:
"""


class ImageData(BaseModel):
    """パースしたドキュメントの画像のクラス"""

    filename: str
    data: str
    type: Literal["jpeg", "png", "gif"]


class IllustSynthesizer:
    """イラストを合成して、新しいイラストを生成する"""

    def __init__(self, model: str = "gpt-4o-2024-05-13") -> None:
        self._model = model
        self._client = OpenAI()

    def _run(
        self,
        prompt: str,
        base64_images: list[ImageData],
    ) -> Optional[str]:
        content: Sequence[ChatCompletionContentPartTextParam | ChatCompletionContentPartImageParam] = [
            ChatCompletionContentPartTextParam(type="text", text=prompt),
        ] + [
            ChatCompletionContentPartImageParam(
                type="image_url",
                image_url={
                    "url": f"data:image/{base64_image.type};base64,{base64_image.data}",
                },
            )
            for base64_image in base64_images
            if len(base64_image.data) <= 20 * 1024 * 1024  # 20MB超えたものは送れない
        ]
        params = ChatCompletionUserMessageParam(
            role="user",
            content=list(content),
        )
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[params],
            max_tokens=1000,
        )
        res = response.choices[0].message.content
        svg_code = res.split("```")[1]
        svg_code = svg_code.lstrip("svg")
        print("Original SVG code:\n", svg_code)

        # svgのimageのhrefをbase64に変換する

        def replace_image_href(match):
            image_filename = match.group(1)
            for image in base64_images:
                if image.filename == image_filename:
                    return f'image href="data:image/{image.type};base64,{image.data}"'
            return match.group(0)

        svg_code_embedded = re.sub(r'image href="(.+?)"', replace_image_href, svg_code)
        return svg_code, svg_code_embedded

    def run(
        self,
        scene: str,
        base64_images: list[ImageData],
    ) -> str:
        prompt = generate_prompt.format(scene=scene, image_filenames="\n".join(image.filename for image in base64_images))
        return self._run(prompt, base64_images)

    def refine(
        self,
        previous_rendered_image: ImageData,
        previous_svg_code: str,
        scene: str,
        base64_images: list[ImageData],
        used_adjusting_points: Optional[list[str]] = None,
    ) -> str:
        if used_adjusting_points is None:
            used_adjusting_points = adjusting_points
        prev_prompt = generate_prompt.format(scene=scene, image_filenames="\n".join(image.filename for image in base64_images))
        prompt = refine_prompt.format(
            output_image=previous_rendered_image.filename,
            previous_prompt=prev_prompt,
            scene=scene,
            previous_svg_code=previous_svg_code,
            image_filenames="\n".join([previous_rendered_image.filename] + [image.filename for image in base64_images]),
            adjusting_points="\n".join(["* レンダリングされたSVGで、" + point for point in used_adjusting_points]),
        )
        return self._run(prompt, [previous_rendered_image] + base64_images)


def load_images(image_paths: list[str]) -> list[ImageData]:
    """画像を読み込む"""
    images = []
    for image_path in image_paths:
        with open(image_path, "rb") as f:
            filename = os.path.basename(image_path)
            data = f.read()
            data = base64.b64encode(data).decode("utf-8")
            images.append(
                ImageData(
                    filename=filename,
                    data=data,
                    type=image_path.split(".")[-1],
                )
            )
    return images


def st_image_grid(image_paths: list[str], row_size: int = 5, width: int = 100, default_checked_images: list[str] = []):
    idx = 0
    while idx < len(image_paths):
        cols_img = st.columns(row_size)
        cols_check = st.columns(row_size)
        for jdx in range(row_size):
            if idx < len(image_paths):
                basename = os.path.basename(image_paths[idx])
                cols_img[jdx].image(
                    image_paths[idx], width=width, caption=basename
                )
                cols_check[jdx].checkbox(
                    "使う", key=f"check_{basename}", value=basename in default_checked_images
                )
                idx += 1
            else:
                break


def st_render_svg(svg):
    """Renders the given svg string."""
    b64 = base64.b64encode(svg.encode('utf-8')).decode("utf-8")
    html = r'<img src="data:image/svg+xml;base64,%s"/>' % b64
    st.write(html, unsafe_allow_html=True)


def generate_svg(image_paths: list[str], scene: str):
    print("Load images...")
    images = load_images(image_paths)
    synthesizer = IllustSynthesizer()
    print("Run synthesizer...")
    svg_code, svg_code_embedded = synthesizer.run(scene, images)
    svg2png(bytestring=svg_code_embedded, write_to="output.png")
    # 生成された画像をrefineする
    print("Refine synthesizer...")
    svg2png(bytestring=svg_code_embedded, write_to="output_refined.png")
    for i in range(3):
        try:
            with open("output_refined.png", "rb") as f:
                data = f.read()
                data = base64.b64encode(data).decode("utf-8")
                rendered_image = ImageData(filename="output_refined.png", data=data, type="png")
            if i == 0:
                used_adjusting_points = [adjusting_points[0], adjusting_points[1]]
            elif i == 1:
                used_adjusting_points = [adjusting_points[1], adjusting_points[2]]
            elif i == 2:
                used_adjusting_points = [adjusting_points[2], adjusting_points[3]]
            else:
                used_adjusting_points = adjusting_points
            _, svg_code_refined_embedded = synthesizer.refine(rendered_image, svg_code, scene, images, used_adjusting_points)
            svg2png(bytestring=svg_code_refined_embedded, write_to="output_refined.png")
        except Exception as e:
            print(e)
            continue
    return svg_code_refined_embedded


if __name__ == "__main__":
    names = ["layoutya"]
    usernames = ["layoutya"]
    passwords = ["layoutya"]
    hashed_passwords = stauth.Hasher(passwords).generate()
    credentials = {
        "usernames": {
            usernames[0]: {
                "name": names[0],
                "password": hashed_passwords[0],
            }
        }
    }
    authenticator = stauth.Authenticate(
        credentials, "layoutya-cookie", "layoutya1234", 30, {"emails": ["nekanat.stock@gmail.com"]}
    )
    authenticator.login("Login", "main")

    if st.session_state["authentication_status"]:
        st.title("れいあうとや")
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../data")
        image_filenames = [f for f in os.listdir(data_dir) if f.endswith(".png")]
        image_paths = [os.path.join(data_dir, path) for path in image_filenames]
        default_checked_images = [
            "stand_naname1_boy.png",
            "stand_naname3_man.png",
            "stand_naname7_woman.png",
            "house_1f.png",
            "school.png",
        ]
        st_image_grid(image_paths, default_checked_images=default_checked_images)
        scene = st.text_area("シーン", "子供が学校へ行くところを見送っている家族の様子")

        if st.button("イラスト生成"):
            image_paths = [
                os.path.join(data_dir, image_filename)
                for image_filename in image_filenames
                if st.session_state[f"check_{image_filename}"]
            ]
            print("Use images:\n", image_paths)
            if len(image_paths) == 0:
                st.error("画像を選択して下さい")
            elif len(image_paths) > 10:
                st.error("画像の選択は10個までにして下さい")
            else:
                with st.spinner("イラスト生成中..."):
                    svg = generate_svg(image_paths, scene)
                st_render_svg(svg)
    elif st.session_state["authentication_status"] == False:
        st.error('Username/password is incorrect')
    elif st.session_state["authentication_status"] == None:
        st.warning('Please enter your username and password')
