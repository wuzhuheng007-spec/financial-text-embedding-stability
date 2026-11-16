from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import validate_password

from .models import UserPreferences, WatchlistItem


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(max_length=150, label="Email")

    def clean_username(self):
        return self.cleaned_data["username"].strip().lower()


class InviteRegistrationForm(forms.Form):
    email = forms.EmailField(max_length=150)
    invite_code = forms.CharField(max_length=100, widget=forms.PasswordInput(render_value=False))
    password1 = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(username=email).exists():
            raise forms.ValidationError("This account cannot be registered.")
        return email

    def clean(self):
        data = super().clean()
        if data.get("password1") and data.get("password2"):
            if data["password1"] != data["password2"]:
                self.add_error("password2", "Passwords do not match.")
            else:
                email = data.get("email", "")
                user = get_user_model()(username=email, email=email)
                validate_password(data["password1"], user=user)
        return data


class WatchlistItemForm(forms.ModelForm):
    class Meta:
        model = WatchlistItem
        fields = ("symbol", "display_name", "asset_type")
        labels = {"symbol": "证券代码", "display_name": "自定义名称（可选）", "asset_type": "资产类型"}

    def clean_symbol(self):
        return self.cleaned_data["symbol"].strip().upper()


class UserPreferencesForm(forms.ModelForm):
    class Meta:
        model = UserPreferences
        fields = ("display_timezone",)
