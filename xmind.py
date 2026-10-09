import os
import pathlib
import shutil
import subprocess
import sys
from abc import ABCMeta
from abc import abstractmethod
from base64 import b64encode, b64decode

from asarPy import extract_asar, pack_asar
from crypto_plus import CryptoPlus
from crypto_plus.encrypt import encrypt_by_key, decrypt_by_key


class KeyGen(metaclass=ABCMeta):

    @abstractmethod
    def generate(self):
        pass

    @abstractmethod
    def parse(self, licenses):
        pass

    @abstractmethod
    def patch(self):
        return ""

    def run(self, patch=True):
        ciphertext_licenses = self.generate()
        print(f"ciphertext_licenses: \n{ciphertext_licenses}")
        if patch:
            patch_info = self.patch()
            if patch_info:
                print(f"patch: \n{patch_info}")
        plaintext_licenses = self.parse(ciphertext_licenses)
        print(f'plaintext_licenses: \n{plaintext_licenses}')


def _find_macos_resources_dir():
    """Locate the Xmind resources dir on macOS.

    macOS:   /Applications/Xmind.app/Contents/Resources
             or ~/Applications/Xmind.app/Contents/Resources
    Override with env XMIND_RESOURCES_DIR when Xmind lives elsewhere.
    """
    override = os.environ.get("XMIND_RESOURCES_DIR")
    if override:
        return pathlib.Path(override)

    candidates = [
        pathlib.Path("/Applications/Xmind.app/Contents/Resources"),
        pathlib.Path.home() / "Applications/Xmind.app/Contents/Resources",
    ]
    for p in candidates:
        if p.joinpath("app.asar").exists():
            return p
    return candidates[0]


class XmindKeyGen(KeyGen):

    def __init__(self):
        if sys.platform == "win32":
            # ---------------- Windows: 原始实现，与最初版本一致 ----------------
            tmp_path = os.environ['TMP']
            asar_path = pathlib.Path(tmp_path).parent.joinpath(r'Programs\Xmind\resources')
            self.is_macos = False
        else:
            # ---------------- macOS: 现有实现 ----------------
            self.is_macos = sys.platform == "darwin"
            asar_path = _find_macos_resources_dir()
        self.resources_dir = asar_path
        self.asar_file = asar_path.joinpath('app.asar')
        self.asar_file_bak = asar_path.joinpath('app.asar.bak')
        self.crack_asar_dir = asar_path.joinpath('ext')
        self.main_dir = self.crack_asar_dir.joinpath("main")
        self.renderer_dir = self.crack_asar_dir.joinpath("renderer")
        self.private_key = None
        self.public_key = None
        self.old_public_key = open('old.pem').read()

    def generate(self):
        if os.path.isfile('key.pem'):
            rsa = CryptoPlus.load('key.pem')
        else:
            rsa = CryptoPlus.generate_rsa(1024)
            rsa.dump("key.pem", "new_public_key.pem")
        license_info = '{"status": "sub", "expireTime": 4093057076000, "ss": "", "deviceId": "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"}'
        self.public_key = rsa.public_key
        self.private_key = rsa.private_key
        self.license_data = b64encode(encrypt_by_key(rsa.private_key, license_info.encode()))
        return self.license_data

    def parse(self, licenses):
        return decrypt_by_key(self.public_key, b64decode(licenses))

    # ==================== Windows 分支（原始行为，勿改动） ====================

    def _patch_windows(self):
        # 解包
        extract_asar(str(self.asar_file), str(self.crack_asar_dir))
        shutil.copytree('crack', self.main_dir, dirs_exist_ok=True)
        # 注入
        with open(self.main_dir.joinpath('main.js'), 'rb') as f:
            lines = f.readlines()
            lines[5] = b'require("./hook")\n'
        with open(self.main_dir.joinpath('main.js'), 'wb') as f:
            f.writelines(lines)
        # 替换密钥
        old_key = f"String.fromCharCode({','.join([str(i) for i in self.old_public_key.encode()])})".encode()
        new_key = f"String.fromCharCode({','.join([str(i) for i in self.public_key.export_key()])})".encode()
        for js_file in self.renderer_dir.rglob("*.js"):
            with open(js_file, 'rb') as f:
                byte_str = f.read()
                index = byte_str.find(old_key)
                if index != -1:
                    byte_str.replace(old_key, new_key)
                    with open(js_file, 'wb') as _f:
                        _f.write(byte_str.replace(old_key, new_key))
                    print(js_file)
                    break
        # 占位符填充
        with open(self.main_dir.joinpath('hook.js'), 'r', encoding='u8') as f:
            content = f.read()
            content = content.replace("{{license_data}}", self.license_data.decode())
        with open(self.main_dir.joinpath('hook.js'), 'w', encoding='u8') as f:
            f.write(content)
        with open(self.main_dir.joinpath('hook').joinpath('crypto.js'), 'r', encoding='u8') as f:
            content = f.read()
            content = content.replace("{{old_public_key}}", self.old_public_key.replace("\n", "\\n"))
            content = content.replace("{{new_public_key}}", self.public_key.export_key().decode().replace("\n", "\\n"))
        with open(self.main_dir.joinpath('hook').joinpath('crypto.js'), 'w', encoding='u8') as f:
            f.write(content)
        # 封包
        os.remove(self.asar_file)
        pack_asar(self.crack_asar_dir, self.asar_file)
        shutil.rmtree(self.crack_asar_dir)

    # ==================== macOS 分支（现有实现，勿改动） ====================

    def _inject_hook(self):
        """Inject require('./hook') into main/main.js.

        macOS builds ship a single-line webpack bundle: replacing `lines[5]`
        would raise IndexError, so we append the require at the end of the
        file instead (webpack's IIFE already ran, `require` here is Node's
        CommonJS require and resolves ./hook.js).
        """
        main_js = self.main_dir.joinpath("main.js")
        with open(main_js, "rb") as f:
            content = f.read()
        if b'require("./hook")' in content:
            return
        if not content.endswith(b"\n"):
            content += b"\n"
        content += b'require("./hook");\n'
        with open(main_js, "wb") as f:
            f.write(content)

    def _sign_app(self):
        """Re-seal the macOS app bundle after touching app.asar.

        Modifying app.asar invalidates the original Developer-ID signature
        seal; without re-signing macOS refuses to launch the app. We sign
        ad-hoc (-) which is enough for local use. Also strip the quarantine
        xattr in case the app was downloaded.
        """
        if not self.is_macos:
            return
        app_bundle = self.resources_dir.parent.parent
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(app_bundle)],
                       capture_output=True)
        r = subprocess.run(
            ["codesign", "--force", "--deep", "--sign", "-", str(app_bundle)],
            capture_output=True, text=True)
        if r.returncode != 0:
            print("[!] 重新签名失败:", r.stderr)
        else:
            print("已重新签名(ad-hoc):", app_bundle)
            verify = subprocess.run(["codesign", "--verify", "--deep", "--strict",
                                     str(app_bundle)], capture_output=True, text=True)
            if verify.returncode == 0:
                print("签名校验通过")
            else:
                print("[!] 签名校验未通过:", verify.stderr)

    def _patch_macos(self):
        # 解包
        extract_asar(str(self.asar_file), str(self.crack_asar_dir))
        shutil.copytree("crack", self.main_dir, dirs_exist_ok=True)
        # 注入
        self._inject_hook()
        # 替换密钥
        old_key = f"String.fromCharCode({','.join([str(i) for i in self.old_public_key.encode()])})".encode()
        new_key = f"String.fromCharCode({','.join([str(i) for i in self.public_key.export_key()])})".encode()
        for js_file in self.renderer_dir.rglob("*.js"):
            with open(js_file, "rb") as f:
                byte_str = f.read()
            index = byte_str.find(old_key)
            if index != -1:
                with open(js_file, "wb") as _f:
                    _f.write(byte_str.replace(old_key, new_key))
                print(js_file)
                break
        # 占位符填充
        with open(self.main_dir.joinpath("hook.js"), "r", encoding="u8") as f:
            content = f.read()
            content = content.replace("{{license_data}}", self.license_data.decode())
        with open(self.main_dir.joinpath("hook.js"), "w", encoding="u8") as f:
            f.write(content)
        with open(self.main_dir.joinpath("hook").joinpath("crypto.js"), "r", encoding="u8") as f:
            content = f.read()
            content = content.replace("{{old_public_key}}", self.old_public_key.replace("\n", "\\n"))
            content = content.replace("{{new_public_key}}", self.public_key.export_key().decode().replace("\n", "\\n"))
        with open(self.main_dir.joinpath("hook").joinpath("crypto.js"), "w", encoding="u8") as f:
            f.write(content)
        # 封包（替换前保留原始 asar 备份）
        if not self.asar_file_bak.exists():
            shutil.copy2(self.asar_file, self.asar_file_bak)
            print("已备份原始 asar:", self.asar_file_bak)
        os.remove(self.asar_file)
        pack_asar(self.crack_asar_dir, self.asar_file)
        shutil.rmtree(self.crack_asar_dir)
        # macOS 下重签，否则应用无法启动
        self._sign_app()

    def patch(self):
        if sys.platform == "win32":
            self._patch_windows()
        else:
            self._patch_macos()


if __name__ == '__main__':
    XmindKeyGen().run()
