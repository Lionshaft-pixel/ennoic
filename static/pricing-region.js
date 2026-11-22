(function (root, factory) {
  var region = factory();
  if (typeof module === "object" && module.exports) module.exports = region;
  if (root) root.EnnoicPricingRegion = region;
})(typeof window !== "undefined" ? window : globalThis, function () {
  function isIndiaPricing(selectedCurrency, preview) {
    var data = preview && preview.data;
    var currency = data && data.currencyCode;
    var address = data && data.details && data.details.address;
    var country = address && address.countryCode;

    if (country && String(country).toUpperCase() === "IN") return true;
    if (currency) return String(currency).toUpperCase() === "INR";
    return String(selectedCurrency || "").toUpperCase() === "INR";
  }

  return { isIndiaPricing: isIndiaPricing };
});
