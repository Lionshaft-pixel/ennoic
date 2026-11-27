(function (root, factory) {
  var region = factory();
  if (typeof module === "object" && module.exports) module.exports = region;
  if (root) root.EnnoicPricingRegion = region;
})(typeof window !== "undefined" ? window : globalThis, function () {
  function isIndiaPricing(selectedCurrency, preview) {
    var data = preview && preview.data;
    var currency = data && data.currencyCode;
    var address = data && data.address;
    var country = address && address.countryCode;

    // Paddle only offers UPI for INR checkouts with an Indian address. Until a
    // preview arrives, the selected INR region is a useful disclosure fallback.
    if (country && String(country).toUpperCase() !== "IN") return false;
    return String(currency || selectedCurrency || "").toUpperCase() === "INR";
  }

  return { isIndiaPricing: isIndiaPricing };
});
